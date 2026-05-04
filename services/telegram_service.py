"""
Cliente HTTP para a Telegram Bot API.
Gerencia envio de mensagens, voz, e download de mídias.
"""

import httpx

from utils.logger import logger


class TelegramService:
    """Serviço para interagir com a API do Telegram Bot."""

    BASE_URL = "https://api.telegram.org"

    def __init__(self):
        self._client: httpx.AsyncClient | None = None

    async def startup(self):
        self._client = httpx.AsyncClient(timeout=30.0)

    async def shutdown(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("TelegramService.startup() was not called")
        return self._client

    def _api_url(self, bot_token: str, method: str) -> str:
        return f"{self.BASE_URL}/bot{bot_token}/{method}"

    async def get_me(self, bot_token: str) -> dict | None:
        """Valida o token e retorna info do bot. Usado no Test Connection."""
        try:
            resp = await self.client.get(self._api_url(bot_token, "getMe"))
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    return data["result"]
            logger.error(f"Telegram getMe falhou: status={resp.status_code} body={resp.text[:300]}")
            return None
        except Exception as e:
            logger.error(f"Telegram getMe exception: {e}")
            return None

    async def set_webhook(self, bot_token: str, webhook_url: str) -> bool:
        """Registra a URL de webhook no Telegram para receber updates."""
        try:
            resp = await self.client.post(
                self._api_url(bot_token, "setWebhook"),
                json={
                    "url": webhook_url,
                    "allowed_updates": ["message", "edited_message", "callback_query"],
                    "drop_pending_updates": True,
                },
            )
            if resp.status_code == 200 and resp.json().get("ok"):
                logger.info(f"Webhook do Telegram registrado: {webhook_url}")
                return True
            logger.error(f"Falha setWebhook: {resp.text}")
            return False
        except Exception as e:
            logger.error(f"setWebhook exception: {e}")
            return False

    async def send_text(self, bot_token: str, chat_id: int | str, text: str) -> dict | None:
        """Envia mensagem de texto. POST /sendMessage"""
        try:
            resp = await self.client.post(
                self._api_url(bot_token, "sendMessage"),
                json={"chat_id": chat_id, "text": text},
            )
            if resp.status_code == 200:
                return resp.json().get("result")
            logger.error(f"sendMessage falhou: {resp.text}")
            return None
        except Exception as e:
            logger.error(f"sendMessage exception: {e}")
            return None

    async def send_voice(self, bot_token: str, chat_id: int | str, voice_bytes: bytes) -> dict | None:
        """Envia áudio como voz (formato OGG/Opus). POST /sendVoice"""
        try:
            resp = await self.client.post(
                self._api_url(bot_token, "sendVoice"),
                data={"chat_id": str(chat_id)},
                files={"voice": ("voice.ogg", voice_bytes, "audio/ogg")},
            )
            if resp.status_code == 200:
                return resp.json().get("result")
            logger.error(f"sendVoice falhou: {resp.text}")
            return None
        except Exception as e:
            logger.error(f"sendVoice exception: {e}")
            return None

    async def get_file_url(self, bot_token: str, file_id: str) -> str | None:
        """
        Retorna a URL pública pra baixar uma mídia recebida.
        Telegram envia file_id; precisamos chamar getFile pra descobrir o file_path.
        """
        try:
            resp = await self.client.get(
                self._api_url(bot_token, "getFile"),
                params={"file_id": file_id},
            )
            if resp.status_code == 200 and resp.json().get("ok"):
                file_path = resp.json()["result"]["file_path"]
                return f"{self.BASE_URL}/file/bot{bot_token}/{file_path}"
            return None
        except Exception as e:
            logger.error(f"getFile exception: {e}")
            return None


telegram_service = TelegramService()
