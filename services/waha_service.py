"""
Cliente HTTP para o WAHA (WhatsApp HTTP API self-host).

Singleton com httpx.AsyncClient compartilhado (mesmo padrão de telegram_service).
Cada método recebe base_url + api_key + session porque a config é por-tenant.
Autenticação: header `X-Api-Key`.
"""

import httpx

from utils.logger import logger


class WAHAService:
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
            raise RuntimeError("WAHAService.startup() was not called")
        return self._client

    def _headers(self, api_key: str | None) -> dict:
        return {"X-Api-Key": api_key} if api_key else {}

    async def _post(self, base_url: str, api_key: str | None, path: str, body: dict) -> dict | None:
        try:
            resp = await self.client.post(
                f"{base_url.rstrip('/')}{path}",
                json=body,
                headers=self._headers(api_key),
            )
            if resp.status_code in (200, 201):
                try:
                    return resp.json()
                except Exception:
                    return {}
            logger.error(f"WAHA {path} falhou: status={resp.status_code} body={resp.text[:300]}")
            return None
        except Exception as e:
            logger.error(f"WAHA {path} exception: {e}")
            return None

    # ── Envio ──

    async def send_text(self, base_url, api_key, session, chat_id, text) -> dict | None:
        return await self._post(base_url, api_key, "/api/sendText", {
            "session": session, "chatId": chat_id, "text": text,
        })

    async def send_image(self, base_url, api_key, session, chat_id, image_url, caption="", mimetype="image/jpeg") -> dict | None:
        return await self._post(base_url, api_key, "/api/sendImage", {
            "session": session, "chatId": chat_id,
            "file": {"mimetype": mimetype, "url": image_url},
            "caption": caption,
        })

    async def send_voice(self, base_url, api_key, session, chat_id, audio_b64) -> dict | None:
        # WhatsApp exige OGG/OPUS; nosso TTS já entrega audio/ogg (base64).
        return await self._post(base_url, api_key, "/api/sendVoice", {
            "session": session, "chatId": chat_id,
            "file": {"mimetype": "audio/ogg; codecs=opus", "data": audio_b64},
            "convert": False,
        })

    # ── Sessão / webhook ──

    async def get_session(self, base_url, api_key, session) -> dict | None:
        try:
            resp = await self.client.get(
                f"{base_url.rstrip('/')}/api/sessions/{session}",
                headers=self._headers(api_key),
            )
            if resp.status_code == 200:
                return resp.json()
            return None
        except Exception as e:
            logger.error(f"WAHA get_session exception: {e}")
            return None

    async def set_session_webhook(self, base_url, api_key, session, webhook_url, events, hmac_key=None) -> bool:
        """Configura o webhook da sessão (PUT /api/sessions/{session}).
        Best-effort — validar contra a instância WAHA ao integrar ao vivo."""
        webhook: dict = {"url": webhook_url, "events": events}
        if hmac_key:
            webhook["hmac"] = {"key": hmac_key}
        body = {"config": {"webhooks": [webhook]}}
        try:
            resp = await self.client.put(
                f"{base_url.rstrip('/')}/api/sessions/{session}",
                json=body,
                headers=self._headers(api_key),
            )
            ok = resp.status_code in (200, 201)
            if not ok:
                logger.error(f"WAHA set_session_webhook falhou: {resp.status_code} {resp.text[:200]}")
            return ok
        except Exception as e:
            logger.error(f"WAHA set_session_webhook exception: {e}")
            return False


waha_service = WAHAService()
