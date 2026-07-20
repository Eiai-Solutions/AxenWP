"""
Pipeline de entrada agnóstico de provedor.

Recebe uma `ParsedMessage` já normalizada por um ChannelAdapter e conduz o turno
inteiro: resolve o contato no CRM, espelha a mensagem, decide se a IA responde,
acumula mensagens numa janela de debounce e devolve a resposta pelo MESMO
adapter que trouxe a mensagem.

Por que existe: `webhooks/zapi_receiver.py` faz tudo isso inline chamando
`zapi_service` diretamente, o que prendeu o motor a um provedor só — e o
`telegram_receiver.py` provou o custo disso ao reimplementar debounce e chunking
e ficar sem o espelho no CRM. Aqui a única coisa específica de provedor é o
adapter recebido por parâmetro.

Estado: hoje serve o WAHA. Z-API e Telegram continuam nos seus receivers e
devem migrar para cá — enquanto não migram, a lógica vive em dois lugares, o que
é dívida consciente e não um convite a divergir.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import OrderedDict
from typing import Any, Dict, Optional

from auth.token_manager import token_manager
from channels.base import ParsedMessage
from services.ghl_service import ghl_service
from utils import metrics
from utils.logger import logger

DEFAULT_DEBOUNCE_SECONDS = 1.5

# Buffers de debounce por contato. Mesmos limites do receiver da Z-API.
_DEBOUNCE_HARD_CAP = 2000
_pending_tasks: Dict[str, asyncio.Task] = {}
_message_buffers: Dict[str, list] = {}
_debounce_config: Dict[str, float] = {}

# Ids de mensagens que NÓS enviamos. O WAHA reentrega as próprias mensagens
# (capabilities.provider_reechoes_own_msgs), então sem isso o agente responderia
# a si mesmo em loop.
_SENT_IDS_MAX_AGE = 300
_SENT_IDS_HARD_CAP = 5000
_sent_message_ids: "OrderedDict[str, float]" = OrderedDict()


def track_sent_message(provider_message_id: Optional[str]) -> None:
    if not provider_message_id:
        return
    _sent_message_ids[provider_message_id] = time.time()
    while len(_sent_message_ids) > _SENT_IDS_HARD_CAP:
        _sent_message_ids.popitem(last=False)


def was_sent_by_us(provider_message_id: Optional[str]) -> bool:
    return bool(provider_message_id and provider_message_id in _sent_message_ids)


def cleanup_stale_entries() -> None:
    """Chamado pelo scheduler: expira ids antigos e buffers de tasks concluídas."""
    agora = time.time()
    velhos = [k for k, t in _sent_message_ids.items() if agora - t > _SENT_IDS_MAX_AGE]
    for k in velhos:
        _sent_message_ids.pop(k, None)

    concluidas = [k for k, t in _pending_tasks.items() if t.done()]
    for k in concluidas:
        _pending_tasks.pop(k, None)
        _message_buffers.pop(k, None)
        _debounce_config.pop(k, None)
    if velhos or concluidas:
        logger.debug(
            f"[PIPELINE] cleanup: {len(velhos)} ids expirados, {len(concluidas)} buffers liberados."
        )


def split_chunks(text: str) -> list:
    """Quebra a resposta da IA em mensagens separadas por linha em branco."""
    chunks = [c.strip() for c in re.split(r"\n\n+", text or "") if c.strip()]
    return chunks or ([text.strip()] if text and text.strip() else [])


def _debounce_seconds(location_id: str) -> float:
    from data.database import SessionLocal
    from data.models import AIAgent

    db = SessionLocal()
    try:
        agent = db.query(AIAgent).filter(AIAgent.location_id == location_id).first()
        if agent and agent.debounce_seconds is not None:
            return float(agent.debounce_seconds)
    except Exception:
        pass
    finally:
        db.close()
    return DEFAULT_DEBOUNCE_SECONDS


# ── CRM: contato e espelho ──

async def resolve_contact_id(
    location_id: str, sender_id: str, sender_name: str, sender_lid: Optional[str] = None
) -> Optional[str]:
    """
    Acha (ou cria) o contato no CRM. Cache local primeiro — vale sobretudo para @lid.

    A pessoa tem até duas identidades (telefone e @lid) e pode chegar por
    qualquer uma delas. Procuramos pelas duas antes de criar, e gravamos as duas
    juntas depois — senão a mesma pessoa vira dois contatos assim que o WhatsApp
    deixar de mandar o número.
    """
    contact_id = token_manager.get_mapped_contact_id(location_id, sender_id)
    if not contact_id and sender_lid:
        contact_id = token_manager.get_mapped_contact_id(location_id, sender_lid)
    if contact_id:
        return contact_id

    if "@lid" not in sender_id:
        contact = await ghl_service.search_contact_by_phone(location_id, sender_id)
        if contact and "id" in contact:
            contact_id = contact["id"]

    if not contact_id:
        nome = sender_name or ("Lead do WhatsApp (Anúncio)" if "@lid" in sender_id else "")
        logger.info(f"Contato {sender_id} não encontrado. Criando novo no CRM...")
        novo = await ghl_service.create_contact(location_id, sender_id, name=nome)
        if novo and "id" in novo:
            contact_id = novo["id"]

    if contact_id:
        # Se a identidade recebida JÁ é o @lid (não resolvemos o número), gravamos
        # ela também na coluna lid: assim a linha fica auto-descritiva e o dia em
        # que o telefone aparecer, a busca por lid reencontra este mesmo contato.
        lid_conhecido = sender_lid or (sender_id if "@lid" in sender_id else None)
        token_manager.save_contact_mapping(location_id, sender_id, contact_id, lid=lid_conhecido)
    return contact_id


def _contato_foi_deletado(resp: Any) -> bool:
    return bool(
        resp
        and isinstance(resp, dict)
        and resp.get("error")
        and resp.get("status_code") == 400
        and "Contact not found/deleted" in str(resp.get("body", {}))
    )


async def mirror_inbound(tenant, pm: ParsedMessage, contact_id: str) -> Optional[str]:
    """
    Registra a mensagem recebida no CRM. Devolve o contact_id em uso (pode mudar
    se o contato tiver sido apagado no CRM e precisar ser recriado), ou None se falhou.
    """
    location_id = pm.location_id

    async def _enviar(cid: str):
        return await ghl_service.send_inbound_message(
            location_id=location_id,
            phone=pm.sender_id,
            message=pm.text,
            attachments=pm.attachments,
            conversation_provider_id=getattr(tenant, "conversation_provider_id", None),
            contact_id=cid,
        )

    resp = await _enviar(contact_id)

    if _contato_foi_deletado(resp):
        logger.warning(f"Contato {contact_id} deletado no CRM. Limpando cache e recriando...")
        token_manager.delete_contact_mapping(location_id, pm.sender_id)
        if pm.sender_lid:
            # A linha do @lid aponta para o mesmo contato morto; se sobrevivesse,
            # a próxima mensagem reusaria o id inexistente em loop.
            token_manager.delete_contact_mapping(location_id, pm.sender_lid)
        novo_id = await resolve_contact_id(
            location_id, pm.sender_id, pm.sender_name, sender_lid=pm.sender_lid
        )
        if not novo_id:
            return None
        contact_id = novo_id
        resp = await _enviar(contact_id)

    if not resp or (isinstance(resp, dict) and resp.get("error")):
        logger.error(f"Falha ao espelhar inbound ({pm.sender_id}) no CRM para {location_id}.")
        return None

    logger.info(f"Inbound de {pm.sender_id} registrado no CRM ({location_id}).")
    return contact_id


async def _mirror_outbound(tenant, pm: ParsedMessage, contact_id: str, texto: str,
                           provider_message_id: Optional[str]) -> None:
    """Espelha no CRM o que a IA respondeu, e amarra o id do provedor ao id do CRM."""
    resp = await ghl_service.send_inbound_message(
        location_id=pm.location_id,
        phone=pm.sender_id,
        message=texto,
        conversation_provider_id=getattr(tenant, "conversation_provider_id", None),
        contact_id=contact_id,
        direction="outbound",
    )
    if resp and not resp.get("error"):
        ghl_msg_id = resp.get("messageId") or resp.get("id")
        if ghl_msg_id and provider_message_id:
            token_manager.save_message_mapping(provider_message_id, ghl_msg_id, pm.location_id)


# ── Gate da IA ──

async def ai_is_enabled(tenant, location_id: str, sender_id: str, contact_id: Optional[str]) -> bool:
    """whatsapp_only decide pelo agente + lead já qualificado; modo CRM decide pelo campo do contato."""
    if getattr(tenant, "mode", "ghl") == "whatsapp_only":
        from data.database import SessionLocal
        from data.models import AIAgent, QualifiedLead

        db = SessionLocal()
        try:
            agent = db.query(AIAgent).filter(AIAgent.location_id == location_id).first()
            if not (agent and agent.is_active):
                return False
            ja_qualificado = db.query(QualifiedLead).filter(
                QualifiedLead.location_id == location_id,
                QualifiedLead.phone == sender_id,
            ).first()
            if ja_qualificado:
                logger.info(f"Lead {sender_id} já qualificado. IA desativada (whatsapp_only).")
                return False
            return True
        finally:
            db.close()

    return await ghl_service.is_ai_active_for_contact(location_id, contact_id)


# ── Turno da IA ──

async def _run_ai(adapter, tenant, pm: ParsedMessage, contact_id: Optional[str], contact_key: str) -> None:
    """Espera a janela de debounce e responde com tudo que chegou nela."""
    try:
        delay = _debounce_config.pop(contact_key, DEFAULT_DEBOUNCE_SECONDS)
        await asyncio.sleep(delay)

        mensagens = _message_buffers.pop(contact_key, [])
        _pending_tasks.pop(contact_key, None)
        if not mensagens:
            return

        texto = "\n".join(m[0] for m in mensagens if m[0])
        is_audio = any(m[1] for m in mensagens)
        audio_url = next((m[2] for m in reversed(mensagens) if m[1] and m[2]), None)
        if not texto:
            return

        if len(mensagens) > 1:
            logger.info(f"🧠 Debounce: {len(mensagens)} mensagens de {pm.sender_id} viram um turno só.")

        from services.ai_service import ai_service

        resposta = await ai_service.process_incoming_message(
            pm.location_id, pm.sender_id, texto,
            is_audio=is_audio, audio_url=audio_url, channel=pm.channel,
        )
        if not resposta:
            return

        qualified = resposta.get("qualified_data")
        if qualified:
            from services.qualification_handler import handle_qualification

            await handle_qualification(
                location_id=pm.location_id,
                phone=pm.sender_id,
                contact_id=contact_id,
                tenant=tenant,
                qualified_data=qualified,
                summary=resposta.get("qualification_summary", ""),
                channel=pm.channel,
            )

        tipo = resposta.get("type", "text")
        conteudo = resposta.get("content", "")
        espelhar = getattr(tenant, "mode", "ghl") != "whatsapp_only" and contact_id

        logger.info(f"🤖 IA respondeu ({tipo}), enviando via {adapter.provider}...")

        if tipo == "audio":
            res = await adapter.send_audio(tenant, pm.sender_id, conteudo)
            track_sent_message(res.provider_message_id)
            if res.ok and espelhar:
                await _mirror_outbound(tenant, pm, contact_id, "[Mensagem de Áudio enviada pela IA]",
                                       res.provider_message_id)
            return

        for i, chunk in enumerate(split_chunks(conteudo)):
            pausa = 5 if i > 0 else 2
            if i > 0:
                await asyncio.sleep(pausa)
            res = await adapter.send_text(tenant, pm.sender_id, chunk, typing_delay=pausa)
            track_sent_message(res.provider_message_id)
            if not res.ok:
                logger.error(f"[PIPELINE] Falha ao enviar resposta via {adapter.provider} para {pm.sender_id}.")
                continue
            if espelhar:
                await _mirror_outbound(tenant, pm, contact_id, chunk, res.provider_message_id)

    except asyncio.CancelledError:
        # Chegou mensagem nova antes do delay expirar — é o debounce funcionando.
        logger.debug(f"IA debounce resetado para {pm.sender_id} (nova mensagem chegou).")
    except Exception as e:
        logger.error(f"Erro no turno da IA ({pm.sender_id}): {e}", exc_info=True)


def _agendar_ia(adapter, tenant, pm: ParsedMessage, contact_id: Optional[str]) -> None:
    contact_key = f"{pm.location_id}:{pm.sender_id}"

    # Hard cap: em pico com milhares de contatos, descarta buffers de tasks já
    # concluídas antes de aceitar mais um.
    if len(_message_buffers) >= _DEBOUNCE_HARD_CAP:
        for k in [k for k, t in _pending_tasks.items() if t.done()]:
            _pending_tasks.pop(k, None)
            _message_buffers.pop(k, None)
            _debounce_config.pop(k, None)
        if len(_message_buffers) >= _DEBOUNCE_HARD_CAP:
            logger.warning(f"Debounce cheio ({_DEBOUNCE_HARD_CAP}); descartando msg de {contact_key}")
            return

    _message_buffers.setdefault(contact_key, []).append((pm.text, pm.is_audio, pm.audio_url))
    _debounce_config[contact_key] = _debounce_seconds(pm.location_id)

    anterior = _pending_tasks.get(contact_key)
    if anterior and not anterior.done():
        anterior.cancel()

    _pending_tasks[contact_key] = asyncio.create_task(
        _run_ai(adapter, tenant, pm, contact_id, contact_key)
    )


# ── Entrada ──

async def handle_inbound(adapter, tenant, pm: ParsedMessage) -> None:
    """
    Um turno completo: filtra ruído, espelha no CRM e agenda a resposta da IA.

    Os filtros ficam aqui (e não no receiver) porque valem para qualquer
    provedor — em especial o dedup, que é o que impede o agente de responder ao
    eco da própria mensagem em provedores que reentregam o que enviamos.
    """
    if pm.is_group:
        logger.debug(f"[{adapter.provider}] Ignorando mensagem de grupo.")
        return
    if pm.from_me:
        logger.debug(f"[{adapter.provider}] Ignorando mensagem própria (fromMe).")
        return
    if was_sent_by_us(pm.provider_message_id):
        logger.debug(f"[{adapter.provider}] Ignorando eco da mensagem {pm.provider_message_id}.")
        return
    if not (pm.text or pm.attachments):
        logger.debug(f"[{adapter.provider}] Mensagem sem conteúdo utilizável; ignorada.")
        return

    contact_id = None
    if getattr(tenant, "mode", "ghl") != "whatsapp_only":
        contact_id = await resolve_contact_id(
            pm.location_id, pm.sender_id, pm.sender_name, sender_lid=pm.sender_lid
        )
        if not contact_id:
            # Sem contato não há CRM nenhum para consultar (nem o gate da IA);
            # aqui parar é a única saída honesta.
            logger.error(f"Impossível registrar inbound: sem contactId para {pm.sender_id}")
            return

        espelhado = await mirror_inbound(tenant, pm, contact_id)
        if espelhado:
            contact_id = espelhado
        else:
            # Falha de espelho é problema de registro, não motivo para deixar o
            # cliente sem resposta: o CRM perde a linha, a conversa continua.
            logger.error(
                f"[PIPELINE] Inbound de {pm.sender_id} não foi espelhado no CRM "
                f"({pm.location_id}); seguindo com a IA mesmo assim."
            )
            metrics.inc("axenwp_crm_mirror_failed_total", labels={"channel": pm.channel})

    try:
        if await ai_is_enabled(tenant, pm.location_id, pm.sender_id, contact_id):
            _agendar_ia(adapter, tenant, pm, contact_id)
    except Exception as e:
        logger.error(f"Erro ao agendar o motor de IA: {e}")
