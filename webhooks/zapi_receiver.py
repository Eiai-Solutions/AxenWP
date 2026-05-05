"""
Recebe webhooks do Z-API (Inbound).
Quando o cliente responde no WhatsApp, o Z-API avisa este servidor,
que formata e insere no GHL via /conversations/messages/inbound
"""

import asyncio
from fastapi import APIRouter, Request, BackgroundTasks, Path
from typing import Dict, Any

from utils.logger import logger
from utils.config import settings
from auth.token_manager import token_manager
from services.ghl_service import ghl_service
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

# Dedup: guarda os zapiMessageId que NÓS enviamos para ignorar quando voltarem como callback
_sent_message_ids: Dict[str, float] = {}           # zapiMessageId -> timestamp
_SENT_IDS_MAX_AGE = 300  # 5 minutos


def _track_sent_message(zapi_message_id: str):
    """Registra um messageId enviado por nós para evitar reprocessamento via callback."""
    import time
    if zapi_message_id:
        _sent_message_ids[zapi_message_id] = time.time()


def cleanup_stale_debounce_entries():
    """Remove entries from debounce dicts whose tasks are done (completed/failed).
    Called periodically via APScheduler to prevent minor memory leaks."""
    import time
    stale_keys = [k for k, t in _ai_pending_tasks.items() if t.done()]
    for key in stale_keys:
        _ai_pending_tasks.pop(key, None)
        _ai_message_buffers.pop(key, None)
        _ai_debounce_config.pop(key, None)
    # Limpa messageIds antigos (>5min)
    now = time.time()
    stale_ids = [mid for mid, ts in _sent_message_ids.items() if now - ts > _SENT_IDS_MAX_AGE]
    for mid in stale_ids:
        _sent_message_ids.pop(mid, None)
    if stale_keys or stale_ids:
        logger.debug(f"Cleanup: {len(stale_keys)} debounce, {len(stale_ids)} sent_ids removidos.")


async def _handle_qualification(location_id: str, phone: str, contact_id: str, tenant, qualified_data: dict, summary: str, channel: str = "whatsapp"):
    """Cria oportunidade no GHL e desativa a IA para o contato após qualificação."""
    from data.database import SessionLocal as _SLQ
    from data.models import AIAgent as _AIAgentQ, QualifiedLead

    is_whatsapp_only = getattr(tenant, "mode", "ghl") == "whatsapp_only"

    # Carregar config do agente
    _dbq = _SLQ()
    try:
        agent = _dbq.query(_AIAgentQ).filter(
            _AIAgentQ.location_id == location_id,
            _AIAgentQ.channel == channel,
        ).first()
        if not agent:
            logger.error(f"Qualificação: agente não encontrado para {location_id}")
            return

        pipeline_id = agent.qualification_pipeline_id
        stage_id = agent.qualification_stage_id
        qualification_fields = agent.qualification_fields or []

        # Verificar duplicação
        existing = _dbq.query(QualifiedLead).filter(
            QualifiedLead.location_id == location_id,
            QualifiedLead.phone == phone,
        ).first()
        if existing:
            logger.info(f"Lead {phone} já qualificado anteriormente. Ignorando duplicação.")
            return
    finally:
        _dbq.close()

    opp_id = None

    # Criar oportunidade no GHL se tiver token válido (funciona em qualquer modo)
    has_ghl_token = await token_manager.get_valid_token(location_id) is not None
    logger.info(f"Qualificação [{phone}]: has_token={has_ghl_token}, pipeline={pipeline_id}, stage={stage_id}, whatsapp_only={is_whatsapp_only}, contact_id={contact_id}")

    if has_ghl_token and pipeline_id and stage_id:
        # Se modo whatsapp_only, não temos contact_id do fluxo de mensagens
        # Precisamos criar/buscar o contato no GHL primeiro
        ghl_contact_id = contact_id
        if not ghl_contact_id:
            # Buscar contato existente ou criar novo no GHL
            existing_contact = await ghl_service.search_contact_by_phone(location_id, phone)
            if existing_contact and "id" in existing_contact:
                ghl_contact_id = existing_contact["id"]
            else:
                # Tentar extrair nome dos campos mapeados antes de criar o contato
                _pre_first = None
                for fd in qualification_fields:
                    if fd.get("ghl_field_id") == "contact.firstName" and fd.get("key") in qualified_data:
                        _pre_first = qualified_data[fd["key"]]
                        break
                lead_name_pre = _pre_first or qualified_data.get("nome") or qualified_data.get("name") or qualified_data.get("nome_completo") or phone
                new_contact = await ghl_service.create_contact(location_id, phone, name=lead_name_pre)
                if new_contact and "id" in new_contact:
                    ghl_contact_id = new_contact["id"]
                    token_manager.save_contact_mapping(location_id, phone, ghl_contact_id)

        if ghl_contact_id:
            # Separar campos por tipo: contato nativo, oportunidade nativa, custom field
            contact_std_updates = {}
            opp_std_updates = {}
            custom_fields = []

            for field_def in qualification_fields:
                ghl_field_id = field_def.get("ghl_field_id") or ""
                key = field_def.get("key")
                if not key or key not in qualified_data or not ghl_field_id:
                    continue
                value = qualified_data[key]

                if ghl_field_id.startswith("contact."):
                    contact_std_updates[ghl_field_id[len("contact."):]] = value
                elif ghl_field_id.startswith("opportunity."):
                    opp_std_updates[ghl_field_id[len("opportunity."):]] = value
                else:
                    custom_fields.append({"id": ghl_field_id, "field_value": value})

            # Atualizar campos nativos do contato
            if contact_std_updates:
                await ghl_service.update_contact(location_id, ghl_contact_id, contact_std_updates)
                logger.info(f"Contato {ghl_contact_id} atualizado com campos nativos: {list(contact_std_updates.keys())}")

            # Nome do lead: prioridade = firstName mapeado > qualified_data genérico > phone
            first = contact_std_updates.get("firstName", "")
            last = contact_std_updates.get("lastName", "")
            lead_name = (
                f"{first} {last}".strip() if first else None
            ) or (
                qualified_data.get("nome") or qualified_data.get("name") or
                qualified_data.get("nome_completo") or qualified_data.get("full_name") or phone
            )
            # Título da oportunidade: usa o nome do contato automaticamente
            opp_name = opp_std_updates.get("name") or f"{lead_name} - WhatsApp Lead"
            monetary_value = 0.0
            if "monetaryValue" in opp_std_updates:
                try:
                    monetary_value = float(opp_std_updates["monetaryValue"])
                except (ValueError, TypeError):
                    pass

            result = await ghl_service.create_opportunity(
                location_id=location_id,
                pipeline_id=pipeline_id,
                stage_id=stage_id,
                contact_id=ghl_contact_id,
                name=opp_name,
                custom_fields=custom_fields if custom_fields else None,
                notes=summary,
                monetary_value=monetary_value,
            )

            if result and not result.get("error"):
                opp_id = result.get("id")
                logger.info(f"Oportunidade criada para lead {phone}: {opp_id}")
            else:
                logger.error(f"Falha ao criar oportunidade para lead {phone}: {result}")

            # Desativar IA via custom field (apenas se não for whatsapp_only)
            if not is_whatsapp_only:
                field_id = await ghl_service._get_custom_field_id_by_name(location_id, "Status IA")
                if field_id:
                    await ghl_service.update_contact(location_id, ghl_contact_id, {
                        "customFields": [{"id": field_id, "field_value": "Desativada"}]
                    })
                    logger.info(f"Status IA desativado para contato {ghl_contact_id} após qualificação")
        else:
            logger.error(f"Qualificação: não foi possível obter/criar contato GHL para {phone}")

    elif pipeline_id and stage_id and not has_ghl_token:
        logger.warning(f"Qualificação configurada mas sem token GHL para {location_id}. Oportunidade não criada.")

    # Salvar registro de lead qualificado (sempre, mesmo sem GHL)
    _dbq2 = _SLQ()
    try:
        ql = QualifiedLead(
            location_id=location_id,
            phone=phone,
            ghl_opportunity_id=opp_id,
            qualified_data=qualified_data,
            summary=summary,
        )
        _dbq2.add(ql)
        _dbq2.commit()
        logger.info(f"Lead qualificado salvo: {phone} @ {location_id}")
    except Exception as e:
        logger.error(f"Erro ao salvar lead qualificado: {e}")
        _dbq2.rollback()
    finally:
        _dbq2.close()
    # No modo whatsapp_only, QualifiedLead serve como flag de desativação — o AI service já verifica


async def _run_ai_response(location_id: str, phone: str, contact_id: str, tenant, contact_key: str):
    """Aguarda o debounce e depois processa a IA com todas as mensagens acumuladas."""
    try:
        delay = _ai_debounce_config.pop(contact_key, DEFAULT_DEBOUNCE_SECONDS)
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

        ai_response = await ai_service.process_incoming_message(
            location_id, phone, combined_text, is_audio=is_audio, audio_url=audio_url, channel="whatsapp"
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
            if sent_data:
                _track_sent_message(sent_data.get("zapiMessageId"))
            if sent_data and not is_whatsapp_only:
                zapi_message_id = sent_data.get("zapiMessageId")
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
                if sent_data:
                    _track_sent_message(sent_data.get("zapiMessageId"))
                if sent_data and not is_whatsapp_only:
                    zapi_message_id = sent_data.get("zapiMessageId")
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

    phone = payload.get("phone", "")
    message_type = payload.get("type", "")
    is_group = payload.get("isGroup", False)
    from_me = payload.get("fromMe", False)
    msg_id = payload.get("messageId") or payload.get("ids", [None])[0] if payload.get("ids") else payload.get("messageId")

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

    logger.info(f"Processando inbound Z-API para tenant {location_id} (origem: {phone})")

    # Log de estrutura: tipo de payload recebido
    payload_keys = [k for k in payload.keys() if k not in ("phone", "text")]
    if any(k in payload_keys for k in ("audio", "voice", "image", "document", "video")):
        logger.info(f"Z-API payload contém mídia. Chaves de mídia: {payload_keys}")

    content_message = "Mensagem recebida do WhatsApp"
    attachments = []
    is_audio = False
    audio_url = None

    # Parse do tipo de mensagem (Z-API possui várias estruturas)
    if "text" in payload and isinstance(payload["text"], dict):
        content_message = payload["text"].get("message", "")
    elif "image" in payload and isinstance(payload["image"], dict):
        content_message = payload["image"].get("caption", "📸 Imagem recebida")
        if "imageUrl" in payload["image"]:
            attachments.append(payload["image"]["imageUrl"])
    elif "audio" in payload and isinstance(payload["audio"], dict):
        content_message = "🎙️ Áudio recebido"
        is_audio = True
        # Z-API às vezes envia 'audioUrl', outras vezes 'url' ou 'mediaUrl'
        audio_url = (
            payload["audio"].get("audioUrl")
            or payload["audio"].get("url")
            or payload["audio"].get("mediaUrl")
        )
        if audio_url:
            attachments.append(audio_url)
        else:
            logger.warning(
                f"Áudio recebido mas sem URL detectada. Chaves disponíveis em payload.audio: "
                f"{list(payload['audio'].keys())}"
            )
    elif "voice" in payload and isinstance(payload["voice"], dict):
        # Algumas integrações Z-API usam a chave 'voice' em vez de 'audio'
        content_message = "🎙️ Áudio recebido"
        is_audio = True
        audio_url = (
            payload["voice"].get("audioUrl")
            or payload["voice"].get("url")
            or payload["voice"].get("mediaUrl")
        )
        if audio_url:
            attachments.append(audio_url)
        else:
            logger.warning(
                f"Voice recebida mas sem URL. Chaves: {list(payload['voice'].keys())}"
            )
    elif "document" in payload and isinstance(payload["document"], dict):
        content_message = payload["document"].get("fileName", "📄 Documento recebido")
        if "documentUrl" in payload["document"]:
            attachments.append(payload["document"]["documentUrl"])
    
    # Se não conseguimos extrair texto decente mas a Z-API enviou string direto
    if not content_message and isinstance(payload.get("text"), str):
        content_message = payload["text"]

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

    # =========================================================================
    # INTEGRAÇÃO AGENTE IA NATIVO — com debounce anti-duplicata
    # =========================================================================
    try:
        # No modo WhatsApp-only, verifica se o agente IA está ativo diretamente
        # No modo GHL, verifica pelo custom field "Status IA" do contato
        if is_whatsapp_only:
            from data.database import SessionLocal as _SL2
            from data.models import AIAgent as _AIAgent2, QualifiedLead as _QL2
            _db2 = _SL2()
            try:
                _agent2 = _db2.query(_AIAgent2).filter(_AIAgent2.location_id == location_id).first()
                is_ai_active = bool(_agent2 and _agent2.is_active)
                # Se ativo, verificar se o lead já foi qualificado (desativa IA para este contato)
                if is_ai_active:
                    already_qualified = _db2.query(_QL2).filter(
                        _QL2.location_id == location_id,
                        _QL2.phone == phone,
                    ).first()
                    if already_qualified:
                        is_ai_active = False
                        logger.info(f"Lead {phone} já qualificado. IA desativada (whatsapp_only).")
            finally:
                _db2.close()
        else:
            is_ai_active = await ghl_service.is_ai_active_for_contact(location_id, contact_id)

        if is_ai_active:
            contact_key = f"{location_id}:{phone}"

            from data.database import SessionLocal as _SL
            from data.models import AIAgent as _AIAgent
            _db = _SL()
            try:
                _agent = _db.query(_AIAgent).filter(_AIAgent.location_id == location_id).first()
                debounce = float(_agent.debounce_seconds) if _agent and _agent.debounce_seconds is not None else DEFAULT_DEBOUNCE_SECONDS
            except Exception:
                debounce = DEFAULT_DEBOUNCE_SECONDS
            finally:
                _db.close()

            if contact_key not in _ai_message_buffers:
                _ai_message_buffers[contact_key] = []
            _ai_message_buffers[contact_key].append((content_message, is_audio, audio_url))

            _ai_debounce_config[contact_key] = debounce

            existing = _ai_pending_tasks.get(contact_key)
            if existing and not existing.done():
                existing.cancel()

            _ai_pending_tasks[contact_key] = asyncio.create_task(
                _run_ai_response(location_id, phone, contact_id, tenant, contact_key)
            )
    except Exception as ai_e:
        logger.error(f"Erro durante agendamento do motor IA: {ai_e}")


@router.post("/inbound/{location_id}")
async def zapi_inbound_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    location_id: str = Path(..., description="O Location ID do GHL desta empresa"),
):
    """
    URL de Webhook que você vai colar no painel administrativo do Z-API:
    https://seu-servidor.com/webhook/zapi/inbound/{SEU_LOCATION_ID_DO_GHL}
    
    Ex: https://axenwp.meudominio.com/webhook/zapi/inbound/HjiMUOsCCHCjtxzEf8PR
    """
    try:
        payload = await request.json()
    except Exception as e:
        logger.error("Payload Z-API Inbound inválido.")
        return {"success": False, "error": "Invalid JSON"}

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
    await ghl_service.update_message_status(
        location_id=location_id,
        message_id=ghl_message_id,
        status=ghl_status,
        error_message=payload.get("error", "Erro remoto no Z-API") if ghl_status == "failed" else None
    )


@router.post("/status/{location_id}")
async def zapi_status_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    location_id: str = Path(..., description="O Location ID do GHL desta empresa"),
):
    """
    URL de Webhook (onMessageStatus) para colar no Z-API:
    https://seu-servidor.com/webhook/zapi/status/{SEU_LOCATION_ID_DO_GHL}
    """
    try:
        payload = await request.json()
    except Exception as e:
        logger.error("Payload Z-API Status inválido.")
        return {"success": False, "error": "Invalid JSON"}

    background_tasks.add_task(process_status_update, location_id, payload)
    
    return {"success": True}
