"""
Recebe webhooks do Telegram (updates).
Quando um usuário manda mensagem no bot, o Telegram chama este endpoint.
Roteia para o Agente IA do canal "telegram".
"""

import asyncio
import base64
from fastapi import APIRouter, Request, BackgroundTasks, Path

from utils.logger import logger
from auth.token_manager import token_manager
from services.telegram_service import telegram_service
from services.ai_service import AIService

router = APIRouter(prefix="/webhook/telegram", tags=["Webhooks Telegram"])

ai_service = AIService()


def _is_text_message(msg: dict) -> tuple[str, bool, str | None]:
    """Retorna (texto, is_audio, file_id_audio). Texto vem de message.text ou caption."""
    text = msg.get("text") or msg.get("caption") or ""
    voice = msg.get("voice") or msg.get("audio")
    if voice:
        return ("[Áudio]", True, voice.get("file_id"))
    return (text, False, None)


@router.post("/{location_id}")
async def receive_telegram_update(
    location_id: str = Path(..., description="Tenant location_id"),
    request: Request = None,
    background_tasks: BackgroundTasks = None,
):
    """Endpoint que o Telegram chama via setWebhook."""
    try:
        payload = await request.json()
    except Exception as e:
        logger.error(f"Telegram webhook: payload inválido: {e}")
        return {"ok": False}

    msg = payload.get("message") or payload.get("edited_message")
    if not msg:
        return {"ok": True}

    chat = msg.get("chat", {})
    chat_id = chat.get("id")
    if not chat_id:
        return {"ok": True}

    # Ignora mensagens de grupos por enquanto (chat type "private" só)
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
        audio_url = await telegram_service.get_file_url(tenant.telegram_bot_token, audio_file_id)

    if not text and not audio_url:
        return {"ok": True}

    # Processa em background pra não segurar o webhook
    background_tasks.add_task(
        _process_and_reply,
        location_id=location_id,
        chat_id=chat_id,
        text=text,
        is_audio=is_audio,
        audio_url=audio_url,
    )
    return {"ok": True}


async def _process_and_reply(
    location_id: str,
    chat_id: int,
    text: str,
    is_audio: bool,
    audio_url: str | None,
):
    """Roda o agente IA e responde no Telegram."""
    try:
        tenant = token_manager.get_tenant(location_id)
        if not tenant or not tenant.telegram_bot_token:
            return

        # Usa chat_id como remote_jid (não tem @s.whatsapp.net no Telegram)
        result = await ai_service.process_incoming_message(
            location_id=location_id,
            remote_jid=str(chat_id),
            text_content=text,
            is_audio=is_audio,
            audio_url=audio_url,
            channel="telegram",
        )

        if not result:
            logger.info(f"Telegram: agente não retornou resposta para chat {chat_id}")
            return

        # Áudio (TTS) ou texto
        if result.get("type") == "audio" and result.get("content", "").startswith("data:audio"):
            try:
                # Extrai bytes do data URL base64
                b64 = result["content"].split(",", 1)[1]
                voice_bytes = base64.b64decode(b64)
                await telegram_service.send_voice(tenant.telegram_bot_token, chat_id, voice_bytes)
                return
            except Exception as e:
                logger.warning(f"Telegram: falha ao enviar áudio, fallback texto: {e}")
                # Cai pro texto abaixo
                await telegram_service.send_text(
                    tenant.telegram_bot_token, chat_id, result.get("text") or ""
                )
                return

        await telegram_service.send_text(
            tenant.telegram_bot_token, chat_id, result.get("content", "")
        )
    except Exception as e:
        logger.error(f"Telegram: erro ao processar/responder: {e}", exc_info=True)
