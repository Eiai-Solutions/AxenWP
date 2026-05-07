"""
Recebe webhooks do Telegram (updates).
Quando um usuário manda mensagem no bot, o Telegram chama este endpoint.
Roteia para o Agente IA do canal "telegram" com debounce e qualificação,
em paridade com o receiver Z-API.
"""

import asyncio
import base64
import re
from collections import OrderedDict
from typing import Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Path, Request

from auth.token_manager import token_manager
from services.ai_service import AIService
from services.qualification_handler import handle_qualification
from services.telegram_service import telegram_service
from utils.limiter import limiter
from utils.logger import logger
from utils.validators import is_valid_location_id
from utils import metrics


router = APIRouter(prefix="/webhook/telegram", tags=["Webhooks Telegram"])

ai_service = AIService()

# ─────────────────────────────────────────────────────────────────────
# Debounce — agrupa múltiplas mensagens rápidas do mesmo chat em 1 turno
# ─────────────────────────────────────────────────────────────────────
DEFAULT_DEBOUNCE_SECONDS = 1.5
_pending_tasks: Dict[str, asyncio.Task] = {}
_message_buffers: Dict[str, list] = {}  # contact_key -> [(text, is_audio, audio_url), ...]
_DEBOUNCE_HARD_CAP = 2000


def _split_messages_by_separator(text: str) -> list[str]:
    """Quebra resposta multi-mensagem (\\n\\n) em chunks pra envio separado."""
    chunks = [c.strip() for c in re.split(r"\n\n+", text or "") if c.strip()]
    return chunks if chunks else ([text] if text else [])


def _is_text_message(msg: dict) -> tuple[str, bool, Optional[str]]:
    """Retorna (texto, is_audio, file_id_audio). Texto vem de message.text ou caption."""
    text = msg.get("text") or msg.get("caption") or ""
    voice = msg.get("voice") or msg.get("audio")
    if voice:
        return ("[Áudio]", True, voice.get("file_id"))
    return (text, False, None)


@router.post("/{location_id}")
@limiter.limit("120/minute")
async def receive_telegram_update(
    location_id: str = Path(..., description="Tenant location_id"),
    request: Request = None,
    background_tasks: BackgroundTasks = None,
):
    """Endpoint que o Telegram chama via setWebhook."""
    if not is_valid_location_id(location_id):
        metrics.inc(
            "axenwp_webhook_rejected_total",
            labels={"channel": "telegram", "reason": "invalid_location_id"},
        )
        return {"ok": False, "error": "Invalid location_id"}

    try:
        payload = await request.json()
    except Exception as e:
        logger.error(f"Telegram webhook: payload inválido: {e}")
        metrics.inc(
            "axenwp_webhook_rejected_total",
            labels={"channel": "telegram", "reason": "invalid_json"},
        )
        return {"ok": False}

    metrics.inc("axenwp_webhooks_received_total", labels={"channel": "telegram"})

    msg = payload.get("message") or payload.get("edited_message")
    if not msg:
        return {"ok": True}

    chat = msg.get("chat", {})
    chat_id = chat.get("id")
    if not chat_id:
        return {"ok": True}

    # Ignora grupos — só conversas privadas por enquanto
    if chat.get("type") != "private":
        logger.info(f"Telegram: ignorando mensagem de grupo (chat_id={chat_id})")
        return {"ok": True}

    tenant = token_manager.get_tenant(location_id)
    if not tenant or not tenant.telegram_bot_token:
        logger.error(f"Tenant {location_id} sem telegram_bot_token configurado")
        return {"ok": True}

    if not tenant.is_active:
        logger.info(f"Tenant {location_id} inativo, ignorando msg do Telegram")
        return {"ok": True}

    text, is_audio, audio_file_id = _is_text_message(msg)
    audio_url = None
    if is_audio and audio_file_id:
        audio_url = await telegram_service.get_file_url(
            tenant.telegram_bot_token, audio_file_id
        )

    if not text and not audio_url:
        return {"ok": True}

    # Acumula no buffer e agenda processamento debounced
    contact_key = f"{location_id}:{chat_id}"

    # Hard cap: previne memory leak em picos
    if len(_message_buffers) >= _DEBOUNCE_HARD_CAP:
        stale = [k for k, t in _pending_tasks.items() if t.done()]
        for k in stale:
            _pending_tasks.pop(k, None)
            _message_buffers.pop(k, None)
        if len(_message_buffers) >= _DEBOUNCE_HARD_CAP:
            logger.warning(
                f"Telegram debounce buffer cheio, descartando msg de {contact_key}"
            )
            return {"ok": True}

    _message_buffers.setdefault(contact_key, []).append((text, is_audio, audio_url))

    existing = _pending_tasks.get(contact_key)
    if existing and not existing.done():
        existing.cancel()

    task = asyncio.create_task(
        _run_ai_response(location_id=location_id, chat_id=chat_id, contact_key=contact_key)
    )
    _pending_tasks[contact_key] = task

    return {"ok": True}


async def _run_ai_response(location_id: str, chat_id: int, contact_key: str) -> None:
    """Aguarda o debounce e processa as mensagens acumuladas em uma única chamada."""
    try:
        await asyncio.sleep(DEFAULT_DEBOUNCE_SECONDS)

        messages = _message_buffers.pop(contact_key, [])
        _pending_tasks.pop(contact_key, None)

        if not messages:
            return

        combined_text = "\n".join(m[0] for m in messages if m[0])
        is_audio = any(m[1] for m in messages)
        audio_url = next((m[2] for m in reversed(messages) if m[1] and m[2]), None)

        if not combined_text and not audio_url:
            return

        if len(messages) > 1:
            logger.info(
                f"Telegram debounce: {len(messages)} msgs combinadas para chat {chat_id}"
            )

        tenant = token_manager.get_tenant(location_id)
        if not tenant or not tenant.telegram_bot_token:
            return

        result = await ai_service.process_incoming_message(
            location_id=location_id,
            remote_jid=str(chat_id),
            text_content=combined_text,
            is_audio=is_audio,
            audio_url=audio_url,
            channel="telegram",
        )

        if not result:
            logger.info(f"Telegram: agente não retornou resposta para chat {chat_id}")
            return

        # Qualificação (mesma lógica do Z-API): cria opportunity GHL + registra QualifiedLead
        qualified_data = result.get("qualified_data")
        if qualified_data:
            summary = result.get("qualification_summary", "")
            phone = str(chat_id)  # Telegram usa chat_id, não phone
            await handle_qualification(
                location_id=location_id,
                phone=phone,
                contact_id=None,
                tenant=tenant,
                qualified_data=qualified_data,
                summary=summary,
                channel="telegram",
            )

        await _send_reply_to_telegram(tenant.telegram_bot_token, chat_id, result)

    except asyncio.CancelledError:
        logger.debug(f"Telegram debounce cancelado para {contact_key} (nova msg chegou).")
    except Exception as e:
        logger.error(f"Telegram: erro ao processar/responder: {e}", exc_info=True)


async def _send_reply_to_telegram(bot_token: str, chat_id: int, result: dict) -> None:
    """Envia a resposta do agente — áudio único OU texto possivelmente quebrado em chunks."""
    if result.get("type") == "audio" and result.get("content", "").startswith("data:audio"):
        try:
            b64 = result["content"].split(",", 1)[1]
            voice_bytes = base64.b64decode(b64)
            await telegram_service.send_voice(bot_token, chat_id, voice_bytes)
            return
        except Exception as e:
            logger.warning(f"Telegram: falha ao enviar áudio, fallback texto: {e}")
            await telegram_service.send_text(
                bot_token, chat_id, result.get("text") or ""
            )
            return

    # Texto: divide por \n\n e envia cada chunk com pequeno intervalo
    chunks = _split_messages_by_separator(result.get("content", ""))
    for i, chunk in enumerate(chunks):
        if i > 0:
            await asyncio.sleep(1.5)
        await telegram_service.send_text(bot_token, chat_id, chunk)


def cleanup_stale_telegram_debounce() -> None:
    """Limpa entries do buffer cuja task já terminou. Chamado pelo APScheduler."""
    stale = [k for k, t in _pending_tasks.items() if t.done()]
    for k in stale:
        _pending_tasks.pop(k, None)
        _message_buffers.pop(k, None)
    if stale:
        logger.debug(f"Telegram debounce cleanup: {len(stale)} entries removidos.")
