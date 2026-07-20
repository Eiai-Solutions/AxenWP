"""
Cliente HTTP para o WAHA (WhatsApp HTTP API self-host).

Singleton com httpx.AsyncClient compartilhado (mesmo padrão de telegram_service).
Cada método recebe base_url + api_key + session porque a config é por-tenant.
Autenticação: header `X-Api-Key`.
"""

import re
import time
from urllib.parse import quote

import httpx

from utils.logger import logger

# Cache lid -> telefone. O vínculo é fixo no WhatsApp, então o TTL é longo; a
# chave inclui a sessão para não cruzar identidade entre tenants no servidor
# compartilhado. Cap simples (FIFO) para não virar vazamento de memória.
_LID_TTL = 24 * 60 * 60.0
_LID_CACHE_MAX = 5000
_lid_cache: dict = {}

# ─────────────────────────────────────────────────────────────────────
# Config GLOBAL do servidor WAHA.
# O servidor WAHA é um só, configurado uma vez pelo admin (SystemSettings).
# Cada tenant guarda apenas a SUA SESSÃO (o número). Resolvemos o servidor aqui,
# com cache curto, para não consultar o banco no hot-path de envio.
# ─────────────────────────────────────────────────────────────────────
_GLOBAL_CFG_TTL = 60.0
_global_cfg_cache: dict = {"at": 0.0, "url": None, "key": None}


def get_global_waha_config(force: bool = False) -> tuple[str | None, str | None]:
    now = time.time()
    if not force and (now - _global_cfg_cache["at"]) < _GLOBAL_CFG_TTL:
        return _global_cfg_cache["url"], _global_cfg_cache["key"]

    url = key = None
    try:
        from data.database import SessionLocal
        from data.models import SystemSettings
        db = SessionLocal()
        try:
            s = db.query(SystemSettings).first()
            if s:
                url = (s.admin_waha_url or None)
                key = (s.admin_waha_api_key or None)
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"Falha ao ler config global do WAHA: {e}")

    _global_cfg_cache.update({"at": now, "url": url, "key": key})
    return url, key


def invalidate_global_waha_config() -> None:
    """Chamar ao salvar as configurações globais para o cache não servir valor velho."""
    _global_cfg_cache["at"] = 0.0


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

    async def send_voice(self, base_url, api_key, session, chat_id, audio_b64=None, *, audio_url=None) -> dict | None:
        """
        Áudio como PTT. Duas origens, dois formatos:

        - `audio_b64`: nosso TTS, que já entrega audio/ogg — vai em `file.data`
          e dispensa conversão.
        - `audio_url`: anexo do CRM, que pode ser mp3/wav — o WAHA baixa a URL e
          transcodifica. Não declaramos mimetype aqui: quem sabe o tipo é quem
          serve o arquivo.
        """
        if audio_url:
            file_body: dict = {"url": audio_url}
            convert = True
        else:
            file_body = {"mimetype": "audio/ogg; codecs=opus", "data": audio_b64}
            convert = False
        return await self._post(base_url, api_key, "/api/sendVoice", {
            "session": session, "chatId": chat_id,
            "file": file_body,
            "convert": convert,
        })

    async def send_file(self, base_url, api_key, session, chat_id, file_url, filename="documento", mimetype=None) -> dict | None:
        """Documento/arquivo genérico. O WAHA baixa a URL — ela precisa ser pública."""
        file_body: dict = {"url": file_url, "filename": filename}
        if mimetype:
            file_body["mimetype"] = mimetype
        return await self._post(base_url, api_key, "/api/sendFile", {
            "session": session, "chatId": chat_id, "file": file_body,
        })

    # ── Sessão / webhook ──

    async def _get(self, base_url, api_key, path, *, timeout: float | None = None) -> httpx.Response | None:
        try:
            kwargs = {"headers": self._headers(api_key)}
            if timeout is not None:
                kwargs["timeout"] = timeout
            return await self.client.get(f"{base_url.rstrip('/')}{path}", **kwargs)
        except Exception as e:
            logger.error(f"WAHA GET {path} exception: {e}")
            return None

    async def resolve_lid(self, base_url, api_key, session, lid: str) -> str | None:
        """
        Telefone (só dígitos) por trás de um @lid, ou None.

        Fallback para quando o payload não trouxe `Info.SenderAlt` — o caminho
        normal resolve sem I/O nenhum, no parse. O vínculo lid↔telefone é fixo no
        WhatsApp, então cacheamos por sessão (a chave inclui a sessão: dois tenants
        nunca compartilham resolução, mesmo dividindo o servidor WAHA).

        Timeout curto de propósito: isso roda ANTES do espelho no CRM, e é melhor
        cair no comportamento antigo (@lid) do que segurar a mensagem 30s.
        """
        if not (base_url and session and lid):
            return None

        chave = (base_url, session, lid)
        agora = time.monotonic()
        entrada = _lid_cache.get(chave)
        if entrada and (agora - entrada[0]) < _LID_TTL:
            return entrada[1]

        resp = await self._get(
            base_url, api_key, f"/api/{session}/lids/{quote(lid, safe='@')}", timeout=5.0
        )
        if resp is None or resp.status_code != 200:
            # Positivo velho vale mais que nada: se já resolvemos esse lid antes,
            # devolver o valor expirado mantém a identidade estável dentro da
            # conversa. Sem isso, uma falha de rede no meio faria a mesma pessoa
            # virar dois contatos no CRM e duas janelas de debounce.
            if entrada and entrada[1]:
                logger.warning(f"[WAHA] LID {lid}: lookup falhou, mantendo resolução anterior.")
                return entrada[1]
            return None

        try:
            pn = (resp.json() or {}).get("pn") or ""
        except Exception:
            return None

        fone = re.match(r"^(\d{6,})", pn)
        fone = fone.group(1) if fone else None
        if fone:
            if len(_lid_cache) >= _LID_CACHE_MAX:
                _lid_cache.pop(next(iter(_lid_cache)))
            _lid_cache[chave] = (agora, fone)
        return fone

    async def list_sessions(self, base_url, api_key) -> list | None:
        resp = await self._get(base_url, api_key, "/api/sessions?all=true")
        if resp is not None and resp.status_code == 200:
            data = resp.json()
            return data if isinstance(data, list) else None
        return None

    async def get_session(self, base_url, api_key, session) -> dict | None:
        resp = await self._get(base_url, api_key, f"/api/sessions/{session}")
        if resp is not None and resp.status_code == 200:
            return resp.json()
        return None

    async def create_session(self, base_url, api_key, session, webhook_url=None,
                             events=None, hmac_key=None, start=True) -> dict | None:
        """Cria (e inicia) uma sessão. Webhook configurado por-sessão apontando
        para o location_id do tenant. Se já existir, cai no start()."""
        config: dict = {}
        if webhook_url:
            webhook: dict = {"url": webhook_url, "events": events or ["message", "session.status"]}
            if hmac_key:
                webhook["hmac"] = {"key": hmac_key}
            config["webhooks"] = [webhook]
        body = {"name": session, "start": start}
        if config:
            body["config"] = config
        resp = await self._post(base_url, api_key, "/api/sessions", body)
        if resp is not None:
            return resp
        # Já existe (422/409) -> garante que está iniciada e atualiza o webhook.
        if config:
            await self.set_session_webhook(base_url, api_key, session,
                                           webhook_url, events or ["message", "session.status"], hmac_key)
        await self.start_session(base_url, api_key, session)
        return await self.get_session(base_url, api_key, session)

    async def _session_action(self, base_url, api_key, session, action) -> bool:
        resp = await self._post(base_url, api_key, f"/api/sessions/{session}/{action}", {})
        return resp is not None

    async def start_session(self, base_url, api_key, session) -> bool:
        return await self._session_action(base_url, api_key, session, "start")

    async def stop_session(self, base_url, api_key, session) -> bool:
        return await self._session_action(base_url, api_key, session, "stop")

    async def logout_session(self, base_url, api_key, session) -> bool:
        return await self._session_action(base_url, api_key, session, "logout")

    async def restart_session(self, base_url, api_key, session) -> bool:
        return await self._session_action(base_url, api_key, session, "restart")

    async def delete_session(self, base_url, api_key, session) -> bool:
        try:
            resp = await self.client.delete(
                f"{base_url.rstrip('/')}/api/sessions/{session}",
                headers=self._headers(api_key),
            )
            return resp.status_code in (200, 201, 204)
        except Exception as e:
            logger.error(f"WAHA delete_session exception: {e}")
            return False

    async def get_qr(self, base_url, api_key, session) -> tuple[bytes, str] | None:
        """Retorna (bytes_da_imagem, content_type) do QR code, para o painel exibir."""
        resp = await self._get(base_url, api_key, f"/api/{session}/auth/qr?format=image")
        if resp is not None and resp.status_code == 200:
            return resp.content, resp.headers.get("content-type", "image/png")
        return None

    async def get_me(self, base_url, api_key, session) -> dict | None:
        """Info do número conectado (após WORKING)."""
        resp = await self._get(base_url, api_key, f"/api/sessions/{session}/me")
        if resp is not None and resp.status_code == 200:
            return resp.json()
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
