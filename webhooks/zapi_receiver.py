"""
Recebe webhooks do Z-API (Inbound).
Quando o cliente responde no WhatsApp, o Z-API avisa este servidor,
que formata e insere no GHL via /conversations/messages/inbound
"""

import asyncio
from collections import deque, OrderedDict
from fastapi import APIRouter, Request, BackgroundTasks, Path
from typing import Any, Dict, Optional

from utils.logger import logger
from utils.config import settings
from utils.validators import is_valid_location_id
from utils.limiter import limiter
from utils import metrics
from auth.token_manager import token_manager
from channels.whatsapp.zapi import ZAPIChannel
from services.channel_accounts import resolver as _resolver_conta
from services.channel_policy import WAHA, active_whatsapp_provider
from services import webhook_auth
from services.ghl_service import ghl_service
from services.message_log import message_type_from_url as msglog_type_from_url
from services.message_log import persist_message as msglog_persist
from services.message_log import update_message_status as msglog_update_status
from services.zapi_service import zapi_service


router = APIRouter(prefix="/webhook/zapi", tags=["Webhooks Z-API"])

# ---------------------------------------------------------------------------
# Debounce: evita múltiplas respostas da IA quando o usuário envia várias
# mensagens em sequência rápida. As mensagens são acumuladas por DEBOUNCE_SECONDS
# e processadas juntas em uma única chamada à IA.
# ---------------------------------------------------------------------------
DEFAULT_DEBOUNCE_SECONDS = 1.5
_ai_pending_tasks: Dict[str, asyncio.Task] = {}   # contact_key -> Task
_ai_message_buffers: Dict[str, list] = {}          # contact_key -> [(text, is_audio, audio_url), ...]
_ai_debounce_config: Dict[str, float] = {}         # contact_key -> debounce_seconds
# contact_key -> account_ref do provedor (`instanceId`). Mesmo molde do debounce: a conta é descoberta
# na porta (`instanceId` do payload) e precisa sobreviver até o flush, que roda noutra
# função. RESSALVA: `contact_key` é `location:phone` e NÃO inclui a conta — o mesmo
# lead escrevendo para dois números cairia no mesmo buffer. Isso é a fase 4
# (`chaves de conversa`); aqui só não se pode piorar.
_ai_conta_por_contato: Dict[str, Optional[int]] = {}

# Buffer dos últimos N payloads recebidos (debug). deque com maxlen evita
# crescimento indefinido — entradas mais antigas são descartadas automaticamente.
_RECENT_WEBHOOKS_MAX = 30
_recent_webhooks: deque = deque(maxlen=_RECENT_WEBHOOKS_MAX)


def get_recent_webhooks() -> list:
    return list(_recent_webhooks)


def _record_webhook(payload: dict, location_id: str | None):
    import time
    safe = {
        "received_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "location_id": location_id,
        "top_level_keys": list(payload.keys()),
        "phone": payload.get("phone"),
        "fromMe": payload.get("fromMe"),
        "type": payload.get("type") or payload.get("messageType"),
        "isStatusReply": payload.get("isStatusReply"),
        "audio_keys": list(payload["audio"].keys()) if isinstance(payload.get("audio"), dict) else None,
        "voice_keys": list(payload["voice"].keys()) if isinstance(payload.get("voice"), dict) else None,
        "audio_url_audioUrl": (payload.get("audio") or {}).get("audioUrl") if isinstance(payload.get("audio"), dict) else None,
        "audio_url_url": (payload.get("audio") or {}).get("url") if isinstance(payload.get("audio"), dict) else None,
    }
    _recent_webhooks.append(safe)


# Dedup: guarda os zapiMessageId que NÓS enviamos para ignorar quando voltarem como callback.
# OrderedDict + cap garante que mesmo entre limpezas do scheduler o uso de memória é limitado.
_SENT_IDS_MAX_AGE = 300         # 5 minutos
_SENT_IDS_HARD_CAP = 5000       # cap absoluto para evitar acúmulo entre cleanups
_sent_message_ids: "OrderedDict[str, float]" = OrderedDict()


# Limites para os buffers de debounce — protege contra picos com muitos contatos simultâneos
_DEBOUNCE_HARD_CAP = 2000


def _track_sent_message(zapi_message_id: str):
    """Registra um messageId enviado por nós para evitar reprocessamento via callback.

    OrderedDict mantém ordem de inserção; quando atinge o cap, removemos o mais antigo.
    """
    import time
    if not zapi_message_id:
        return
    _sent_message_ids[zapi_message_id] = time.time()
    while len(_sent_message_ids) > _SENT_IDS_HARD_CAP:
        _sent_message_ids.popitem(last=False)


def cleanup_stale_debounce_entries():
    """Remove entries from debounce dicts whose tasks are done (completed/failed).
    Called periodically via APScheduler to prevent minor memory leaks."""
    import time
    stale_keys = [k for k, t in _ai_pending_tasks.items() if t.done()]
    for key in stale_keys:
        _ai_pending_tasks.pop(key, None)
        _ai_message_buffers.pop(key, None)
        _ai_debounce_config.pop(key, None)
        _ai_conta_por_contato.pop(key, None)
    # Limpa messageIds antigos (>5min)
    now = time.time()
    stale_ids = [mid for mid, ts in _sent_message_ids.items() if now - ts > _SENT_IDS_MAX_AGE]
    for mid in stale_ids:
        _sent_message_ids.pop(mid, None)
    if stale_keys or stale_ids:
        logger.debug(f"Cleanup: {len(stale_keys)} debounce, {len(stale_ids)} sent_ids removidos.")


async def _handle_qualification(location_id: str, phone: str, contact_id: str, tenant, qualified_data: dict, summary: str, channel: str = "whatsapp"):
    """Wrapper de compatibilidade — delega para services.qualification_handler."""
    from services.qualification_handler import handle_qualification
    await handle_qualification(
        location_id=location_id,
        phone=phone,
        contact_id=contact_id,
        tenant=tenant,
        qualified_data=qualified_data,
        summary=summary,
        channel=channel,
    )



async def _run_ai_response(location_id: str, phone: str, contact_id: str, tenant, contact_key: str):
    """Aguarda o debounce e depois processa a IA com todas as mensagens acumuladas."""
    try:
        delay = _ai_debounce_config.pop(contact_key, DEFAULT_DEBOUNCE_SECONDS)
        account_ref = _ai_conta_por_contato.pop(contact_key, None)
        await asyncio.sleep(delay)

        messages = _ai_message_buffers.pop(contact_key, [])
        _ai_pending_tasks.pop(contact_key, None)

        if not messages:
            return

        # Combina todas as mensagens recebidas na janela de debounce em um único turno
        combined_text = '\n'.join(m[0] for m in messages if m[0])
        is_audio = any(m[1] for m in messages)
        # Pega a URL do último áudio recebido (para transcrição)
        audio_url = None
        for m in reversed(messages):
            if m[1] and m[2]:  # is_audio=True e tem audio_url
                audio_url = m[2]
                break

        if not combined_text:
            return

        if len(messages) > 1:
            logger.info(f"🧠 Debounce: combinando {len(messages)} mensagens de {phone} em uma única chamada IA.")
        else:
            logger.info(f"🧠 Agente IA ativado para contato {contact_id or phone}. Gerando resposta...")

        from services.ai_service import ai_service

        # Uma resolução por TURNO (o pipeline compartilhado faz igual), não por
        # mensagem: o debounce junta várias e todas vieram da mesma conta.
        conta_id = await _resolver_conta(location_id, "whatsapp", account_ref)

        # O gate foi lido ANTES do sono do debounce; reler o estado local aqui é o
        # que impede a IA de responder por cima do operador que assumiu no meio.
        from services import ai_gate as _gate
        if await _gate.pausado_durante_a_espera(location_id, "whatsapp", phone):
            return

        ai_response = await ai_service.process_incoming_message(
            location_id, phone, combined_text, is_audio=is_audio, audio_url=audio_url,
            channel="whatsapp", channel_account_id=conta_id,
        )
        if not ai_response:
            return

        # ── Qualificação: se a IA retornou dados de qualificação ──
        qualified_data = ai_response.get("qualified_data")
        if qualified_data:
            summary = ai_response.get("qualification_summary", "")
            await _handle_qualification(location_id, phone, contact_id, tenant, qualified_data, summary, channel="whatsapp")

        ai_type = ai_response.get("type", "text")
        ai_content = ai_response.get("content", "")

        logger.info(f"🤖 IA respondeu ({ai_type}), enviando via Z-API...")

        is_whatsapp_only = getattr(tenant, "mode", "ghl") == "whatsapp_only"

        if ai_type == "audio":
            sent_data = await zapi_service.send_audio(
                instance_id=tenant.zapi_instance_id,
                token=tenant.zapi_token,
                phone=phone,
                audio_url=ai_content,
                client_token=tenant.zapi_client_token,
                record_audio=True
            )
            zapi_message_id = sent_data.get("zapiMessageId") if sent_data else None
            if sent_data:
                _track_sent_message(zapi_message_id)
            ghl_msg_id = None
            if sent_data and not is_whatsapp_only:
                outbound_resp = await ghl_service.send_inbound_message(
                    location_id=location_id,
                    phone=phone,
                    message="[Mensagem de Áudio enviada pela IA]",
                    conversation_provider_id=tenant.conversation_provider_id,
                    contact_id=contact_id,
                    direction="outbound"
                )
                if outbound_resp and not outbound_resp.get("error"):
                    ghl_msg_id = outbound_resp.get("messageId") or outbound_resp.get("id")
                    if ghl_msg_id and zapi_message_id:
                        token_manager.save_message_mapping(zapi_message_id, ghl_msg_id, location_id)
            await msglog_persist(
                location_id=location_id, channel="whatsapp", provider="zapi",
                direction="outbound", sender_role="ai", contact_ref=phone, ghl_contact_id=contact_id,
                message_type="audio", text="[Áudio da IA]",
                provider_message_id=zapi_message_id, ghl_message_id=ghl_msg_id,
                status="sent" if sent_data else "failed",
            )
        else:
            import re

            chunks = [c.strip() for c in re.split(r'\n\n+', ai_content) if c.strip()]
            if not chunks:
                chunks = [ai_content.strip()]

            for i, chunk in enumerate(chunks):
                delay = 5 if i > 0 else 2
                if i > 0:
                    await asyncio.sleep(delay)

                sent_data = await zapi_service.send_text(
                    instance_id=tenant.zapi_instance_id,
                    token=tenant.zapi_token,
                    phone=phone,
                    message=chunk,
                    client_token=tenant.zapi_client_token,
                    delay_typing=delay
                )
                zapi_message_id = sent_data.get("zapiMessageId") if sent_data else None
                if sent_data:
                    _track_sent_message(zapi_message_id)
                ghl_msg_id = None
                if sent_data and not is_whatsapp_only:
                    outbound_resp = await ghl_service.send_inbound_message(
                        location_id=location_id,
                        phone=phone,
                        message=chunk,
                        conversation_provider_id=tenant.conversation_provider_id,
                        contact_id=contact_id,
                        direction="outbound"
                    )
                    if outbound_resp and not outbound_resp.get("error"):
                        ghl_msg_id = outbound_resp.get("messageId") or outbound_resp.get("id")
                        if ghl_msg_id and zapi_message_id:
                            token_manager.save_message_mapping(zapi_message_id, ghl_msg_id, location_id)
                await msglog_persist(
                    location_id=location_id, channel="whatsapp", provider="zapi",
                    direction="outbound", sender_role="ai", contact_ref=phone, ghl_contact_id=contact_id,
                    text=chunk, provider_message_id=zapi_message_id, ghl_message_id=ghl_msg_id,
                    status="sent" if sent_data else "failed",
                )

    except asyncio.CancelledError:
        # Nova mensagem chegou antes do delay expirar — comportamento esperado do debounce
        logger.debug(f"IA debounce resetado para {phone} (nova mensagem chegou).")
    except Exception as e:
        logger.error(f"Erro no processamento IA (debounce): {e}")


async def process_inbound_message(location_id: str, payload: Dict[str, Any]):
    """
    Processa a mensagem recebida pelo webhook da Z-API.
    1. Verifica o token do provider para esse location_id.
    2. Extrai texto e anexos.
    3. Manda para a API Inbound do GHL.
    """
    tenant = token_manager.get_tenant(location_id)
    if not tenant:
        logger.error(f"Z-API Inbound abortado: Tenant {location_id} não registrado.")
        return

    if not getattr(tenant, 'is_active', True):
        logger.info(f"Z-API Inbound abortado: Automação desativada para {location_id}.")
        return

    # "Indisponível" precisa valer de verdade: se a instância migrou para o WAHA,
    # um webhook velho da Z-API ainda apontado pra cá não pode responder junto —
    # seriam duas pontas atendendo o mesmo número.
    if active_whatsapp_provider(tenant) == WAHA:
        logger.info(f"[CHANNEL] Z-API Inbound ignorado: {location_id} usa WAHA como provedor.")
        metrics.inc("millochat_webhook_rejected_total", labels={"channel": "whatsapp", "reason": "provider_inactive"})
        return

    pm = ZAPIChannel().parse_inbound(location_id, payload)
    phone = pm.sender_id
    message_type = pm.message_type
    is_group = pm.is_group
    from_me = pm.from_me
    msg_id = pm.provider_message_id

    logger.debug(
        f"Z-API webhook raw: location={location_id} type={message_type} "
        f"fromMe={from_me} phone={phone} msgId={msg_id}"
    )

    # Filtrar mensagens indesejadas
    if is_group:
        logger.debug(f"Ignorando mensagem de grupo.")
        return
    if from_me:
        logger.debug(f"Ignorando mensagem fromMe=true.")
        return

    # Dedup: ignorar callbacks de mensagens que NÓS enviamos via Z-API
    if msg_id and msg_id in _sent_message_ids:
        logger.debug(f"Ignorando callback de mensagem enviada por nós (dedup): {msg_id}")
        return

    # Aceita apenas eventos de mensagem recebida
    if message_type not in ["ReceivedCallback", "MessageReceived"]:
        logger.debug(f"Ignorando evento de tipo '{message_type}' (não é mensagem recebida).")
        return

    # Salva snapshot do payload pra debug via endpoint /admin/seed/joorney/recent-webhooks
    try:
        _record_webhook(payload, location_id)
    except Exception:
        pass

    logger.info(f"Processando inbound Z-API para tenant {location_id} (origem: {phone})")

    # Log de estrutura: tipo de payload recebido
    payload_keys = [k for k in payload.keys() if k not in ("phone", "text")]
    if any(k in payload_keys for k in ("audio", "voice", "image", "document", "video")):
        logger.info(f"Z-API payload contém mídia. Chaves de mídia: {payload_keys}")

    # Conteúdo já normalizado pelo ZAPIChannel.parse_inbound (mesma lógica de antes).
    content_message = pm.text
    attachments = pm.attachments
    is_audio = pm.is_audio
    audio_url = pm.audio_url

    is_whatsapp_only = getattr(tenant, "mode", "ghl") == "whatsapp_only"
    contact_id = None

    # =========================================================================
    # MODO GHL: registra contato e mensagem no CRM
    # =========================================================================
    if not is_whatsapp_only:
        # 1. Tentar achar o mapeamento no banco de dados local primeiro (útil para @lid e velocidade)
        contact_id = token_manager.get_mapped_contact_id(location_id, phone)

        if not contact_id:
            if "@lid" not in phone:
                contact = await ghl_service.search_contact_by_phone(location_id, phone)
                if contact and "id" in contact:
                    contact_id = contact["id"]

            if not contact_id:
                logger.info(f"Contato {phone} não encontrado. Criando novo no GHL...")
                sender_name = payload.get("senderName") or payload.get("participantName") or ""
                if not sender_name and "@lid" in phone:
                    sender_name = "Lead do WhatsApp (Anúncio)"

                new_contact = await ghl_service.create_contact(location_id, phone, name=sender_name)
                if new_contact and "id" in new_contact:
                    contact_id = new_contact["id"]

            if contact_id:
                token_manager.save_contact_mapping(location_id, phone, contact_id)

        if not contact_id:
            logger.error(f"Impossível registrar inbound: Falha ao obter/criar contactId para o telefone {phone}")
            return

        # Registrar no CRM
        resp = await ghl_service.send_inbound_message(
            location_id=location_id,
            phone=phone,
            message=content_message,
            attachments=attachments,
            conversation_provider_id=tenant.conversation_provider_id,
            contact_id=contact_id,
        )

        # Detecção de contato deletado no GHL manualmente pelo usuário
        if resp and isinstance(resp, dict) and resp.get("error"):
            if resp.get("status_code") == 400 and "Contact not found/deleted" in str(resp.get("body", {})):
                logger.warning(f"Contato {contact_id} deletado no GHL. Limpando cache e recriando...")
                token_manager.delete_contact_mapping(location_id, phone)

                sender_name = payload.get("senderName") or payload.get("participantName") or ""
                if not sender_name and "@lid" in phone:
                    sender_name = "Lead do WhatsApp (Anúncio)"

                new_contact = await ghl_service.create_contact(location_id, phone, name=sender_name)
                if new_contact and "id" in new_contact:
                    contact_id = new_contact["id"]
                    token_manager.save_contact_mapping(location_id, phone, contact_id)

                    resp = await ghl_service.send_inbound_message(
                        location_id=location_id,
                        phone=phone,
                        message=content_message,
                        attachments=attachments,
                        conversation_provider_id=tenant.conversation_provider_id,
                        contact_id=contact_id,
                    )

        if not resp or resp.get("error"):
            logger.error(f"Falha ao transferir inbound ({phone}) para GHL no tenant {location_id}.")
            return

        logger.info(f"Sucesso ao registrar inbound ({phone}) no GHL para tenant {location_id}.")

    # Log completo (base do painel próprio) — Z-API serve mídia por CDN público,
    # então o anexo já é uma URL utilizável; sem media_filename (não há blob local).
    _anexo = attachments[0] if attachments else None
    await msglog_persist(
        location_id=location_id, channel="whatsapp", provider="zapi",
        direction="inbound", sender_role="contact", contact_ref=phone,
        ghl_contact_id=contact_id, message_type=msglog_type_from_url(_anexo, is_audio),
        text=content_message or None, media_url=_anexo,
        provider_message_id=msg_id, status="delivered",
    )

    # =========================================================================
    # INTEGRAÇÃO AGENTE IA NATIVO — com debounce anti-duplicata
    # =========================================================================
    try:
        # UM gate só, o mesmo do WAHA e do Telegram. Esta era a segunda cópia da
        # regra, e as duas já haviam divergido: o filtro de canal foi corrigido na
        # do `inbound_pipeline` e "ficou para trás" aqui, como o próprio comentário
        # antigo registrava. Duas cópias da mesma ideia só continuam iguais por sorte.
        from services import ai_gate

        is_ai_active = await ai_gate.pode_responder(
            location_id=location_id, channel="whatsapp", contact_ref=phone,
            tenant=tenant, contact_id=contact_id,
        )

        if is_ai_active:
            contact_key = f"{location_id}:{phone}"

            from data.database import SessionLocal as _SL
            from data.models import AIAgent as _AIAgent
            _db = _SL()
            try:
                # Mesmo motivo do bloco acima: a janela de debounce é POR AGENTE, e
                # este caminho é só WhatsApp. Sem o filtro, bastava existir um
                # agente de Telegram para o WhatsApp passar a usar o tempo dele.
                _agent = (
                    _db.query(_AIAgent)
                    .filter(
                        _AIAgent.location_id == location_id,
                        _AIAgent.channel == "whatsapp",
                    )
                    .first()
                )
                debounce = float(_agent.debounce_seconds) if _agent and _agent.debounce_seconds is not None else DEFAULT_DEBOUNCE_SECONDS
            except Exception:
                debounce = DEFAULT_DEBOUNCE_SECONDS
            finally:
                _db.close()

            # Hard cap para evitar memory leak em picos extremos (milhares de
            # contatos diferentes ao mesmo tempo). Se atingiu o limite, descarta
            # entradas com tasks já concluídas antes de aceitar a nova.
            if len(_ai_message_buffers) >= _DEBOUNCE_HARD_CAP:
                stale = [k for k, t in _ai_pending_tasks.items() if t.done()]
                for k in stale:
                    _ai_pending_tasks.pop(k, None)
                    _ai_message_buffers.pop(k, None)
                    _ai_debounce_config.pop(k, None)
                    _ai_conta_por_contato.pop(k, None)
                if len(_ai_message_buffers) >= _DEBOUNCE_HARD_CAP:
                    logger.warning(
                        f"Debounce buffer cheio ({_DEBOUNCE_HARD_CAP}), descartando msg de {contact_key}"
                    )
                    return

            if contact_key not in _ai_message_buffers:
                _ai_message_buffers[contact_key] = []
            _ai_message_buffers[contact_key].append((content_message, is_audio, audio_url))

            _ai_debounce_config[contact_key] = debounce
            # Guarda só a REFERÊNCIA do provedor — dado que já veio no payload, custo
            # zero. Resolver aqui exigia um `await` NO MEIO da sequência
            # append → config → create_task, que antes era síncrona e portanto
            # atômica: qualquer falha nesse await deixava a mensagem órfã no buffer,
            # sem task, invisível aos limpadores. A resolução acontece no flush, uma
            # vez por turno em vez de uma por mensagem.
            _ai_conta_por_contato[contact_key] = pm.account_ref

            existing = _ai_pending_tasks.get(contact_key)
            if existing and not existing.done():
                existing.cancel()

            _ai_pending_tasks[contact_key] = asyncio.create_task(
                _run_ai_response(location_id, phone, contact_id, tenant, contact_key)
            )
    except Exception as ai_e:
        logger.error(f"Erro durante agendamento do motor IA: {ai_e}")


@router.post("/inbound/{location_id}")
@limiter.limit("120/minute")
async def zapi_inbound_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    location_id: str = Path(..., description="O Location ID do GHL desta empresa"),
):
    """
    URL de Webhook que você cola no painel da Z-API:
    https://seu-servidor.com/webhook/zapi/inbound/{SEU_LOCATION_ID}

    O segredo, quando houver, vai no header `x-chat-secret`.
    """
    return await _inbound(request, background_tasks, location_id, None)


@router.post("/inbound/{location_id}/{segredo}")
@limiter.limit("120/minute")
async def zapi_inbound_webhook_com_segredo(
    request: Request,
    background_tasks: BackgroundTasks,
    location_id: str = Path(..., description="O Location ID do GHL desta empresa"),
    segredo: str = Path(..., description="Segredo no caminho"),
):
    """
    Mesma coisa, com o segredo no CAMINHO.

    Existe porque o painel da Z-API pode não permitir header customizado, e um
    segredo no path é melhor que webhook nenhum. É a segunda opção de propósito:
    caminho aparece no log de acesso do proxy reverso, e log é copiado, mandado
    para suporte e guardado por meses. Nunca por querystring — ela tem os mesmos
    defeitos do path e ainda vaza em `Referer`.

    Duas rotas em vez de um parâmetro opcional porque o FastAPI recusa default em
    parâmetro de caminho, e torná-lo opcional o transformaria em QUERYSTRING —
    exatamente o que não se quer.
    """
    return await _inbound(request, background_tasks, location_id, segredo)


async def _inbound(request: Request, background_tasks: BackgroundTasks,
                   location_id: str, segredo: Optional[str]):
    if not is_valid_location_id(location_id):
        logger.warning(f"Z-API inbound: location_id rejeitado por validação ({location_id!r})")
        metrics.inc("millochat_webhook_rejected_total", labels={"channel": "whatsapp", "reason": "invalid_location_id"})
        return {"success": False, "error": "Invalid location_id"}

    # A Z-API não assina o corpo, então o segredo vem por header (preferido) ou
    # pelo fim do caminho, quando o painel dela não deixa mandar header. Até hoje
    # `zapi_webhook_secret` estava CONFIGURADO no ambiente e o código nunca o lia:
    # quem configurou achou que estava protegido e não estava.
    if not webhook_auth.verificar_zapi(request.headers, segredo):
        metrics.inc("millochat_webhook_rejected_total",
                    labels={"channel": "whatsapp", "reason": "invalid_secret"})
        return {"success": False, "error": "Invalid signature"}

    try:
        payload = await request.json()
    except Exception:
        logger.error("Payload Z-API Inbound inválido.")
        metrics.inc("millochat_webhook_rejected_total", labels={"channel": "whatsapp", "reason": "invalid_json"})
        return {"success": False, "error": "Invalid JSON"}

    metrics.inc("millochat_webhooks_received_total", labels={"channel": "whatsapp"})
    # Envia pro processamento em background
    background_tasks.add_task(process_inbound_message, location_id, payload)

    # Se o GHL exigir 200 sempre, Z-API também precisa para parar de reenviar
    return {"success": True}


async def process_status_update(location_id: str, payload: Dict[str, Any]):
    """
    Processa webhooks de STATUS de mensagens (onMessageStatus) da Z-API
    e repassa pro GHL.
    Status esperados da Z-API: "DELIVERED", "READ", "ERROR", etc.
    """
    zapi_message_id = payload.get("messageId")
    status = payload.get("status", "").upper()
    
    if not zapi_message_id:
        return

    tenant = token_manager.get_tenant(location_id)
    if tenant and not getattr(tenant, 'is_active', True):
        logger.info(f"Z-API Status abortado: Automação desativada para {location_id}.")
        return
        
    mapping = token_manager.get_ghl_message_id_by_zapi(zapi_message_id)
    if not mapping:
        logger.debug(f"Webhook de status ignorado: Z-API MessageId {zapi_message_id} não mapeado para GHL.")
        return
        
    ghl_message_id = mapping.get("ghl_message_id")
    
    # Traduzir status da Z-API para o GHL (delivered, read, failed)
    ghl_status = "delivered" # Default fallback seguro
    if status == "DELIVERED":
        ghl_status = "delivered"
    elif status == "READ":
        ghl_status = "read"
    elif status in ["ERROR", "FAILED", "REJECTED"]:
        ghl_status = "failed"
        
    logger.info(f"Atualizando status no GHL para '{ghl_status}' (GHL MsgId: {ghl_message_id})")
    erro = payload.get("error", "Erro remoto no Z-API") if ghl_status == "failed" else None
    await ghl_service.update_message_status(
        location_id=location_id,
        message_id=ghl_message_id,
        status=ghl_status,
        error_message=erro,
    )
    # Espelha o status no nosso log (base do painel próprio).
    await msglog_update_status(location_id, provider_message_id=zapi_message_id, status=ghl_status, error=erro)


@router.post("/status/{location_id}")
@limiter.limit("240/minute")
async def zapi_status_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    location_id: str = Path(..., description="O Location ID do GHL desta empresa"),
):
    """
    URL de Webhook (onMessageStatus) para colar no Z-API:
    https://seu-servidor.com/webhook/zapi/status/{SEU_LOCATION_ID_DO_GHL}
    """
    if not is_valid_location_id(location_id):
        return {"success": False, "error": "Invalid location_id"}

    try:
        payload = await request.json()
    except Exception:
        logger.error("Payload Z-API Status inválido.")
        return {"success": False, "error": "Invalid JSON"}

    background_tasks.add_task(process_status_update, location_id, payload)
    
    return {"success": True}
