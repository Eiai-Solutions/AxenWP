"""
Adapter WAHA (WhatsApp HTTP API self-host).

Implementa a porta ChannelAdapter contra a API REST do WAHA. Ainda NÃO é ligado
ao inbound (a rota universal + pipeline entram no próximo passo); fica pronto
atrás da flag `Tenant.whatsapp_provider == "waha"`.

Diferenças-chave vs Z-API (ver ChannelCapabilities):
- WAHA REENTREGA as próprias mensagens (fromMe) -> dedup por provider_message_id
  é obrigatório (o pipeline compartilhado cuida disso).
- Áudio outbound vai como base64 em file.data (não data-url).
"""

from __future__ import annotations

import re
from typing import Optional

from channels.base import ChannelCapabilities, OutboundResult, ParsedMessage
from services.waha_service import get_global_waha_config, waha_service
from utils.config import settings
from utils.logger import logger

# Sufixos de identidade do WhatsApp. Removemos @c.us/@s.whatsapp.net (contato
# normal) mas PRESERVAMOS @lid (leads de anúncio) para o fluxo GHL, igual à Z-API.
_STRIP_SUFFIX = re.compile(r"@(c\.us|s\.whatsapp\.net)$")


class WAHAChannel:
    channel = "whatsapp"
    provider = "waha"
    capabilities = ChannelCapabilities(
        supports_audio_ptt=True,
        supports_typing_delay=True,
        outbound_media_accepts_data_url=False,  # WAHA precisa base64 em file.data
        provider_reechoes_own_msgs=True,         # WAHA reentrega fromMe -> dedup obrigatório
    )

    # ── Config por tenant ──

    def _cfg(self, tenant) -> tuple[str, str, str]:
        """Servidor vem do config GLOBAL (um WAHA para todos); o tenant guarda só a
        sessão (o número). As colunas waha_base_url/waha_api_key ficam como override
        opcional, para o caso raro de um tenant ter servidor dedicado."""
        base = getattr(tenant, "waha_base_url", None) or ""
        key = getattr(tenant, "waha_api_key", None) or ""
        if not base or not key:
            g_url, g_key = get_global_waha_config()
            base = base or (g_url or "")
            key = key or (g_key or "")
        session = getattr(tenant, "waha_session", None) or getattr(tenant, "location_id", "") or ""
        return base, key, session

    def credentials_ok(self, tenant) -> bool:
        base, key, session = self._cfg(tenant)
        return bool(base and session)

    def _chat_id(self, to: str) -> str:
        return to if "@" in to else f"{to}@c.us"

    @staticmethod
    def _extract_message_id(resp: Optional[dict]) -> Optional[str]:
        if not isinstance(resp, dict):
            return None
        # WAHA devolve o objeto da mensagem enviada; o id pode vir em formas diferentes por engine.
        return (
            resp.get("id")
            or (resp.get("key") or {}).get("id")
            or (resp.get("_data") or {}).get("id")
        )

    # ── Inbound ──

    def parse_inbound(
        self, location_id: str, payload: dict, headers: Optional[dict] = None
    ) -> Optional[ParsedMessage]:
        if payload.get("event") != "message":
            return None  # message.ack / session.status / etc. não são inbound de conversa

        p = payload.get("payload") or {}
        raw_from = p.get("from") or ""
        is_group = raw_from.endswith("@g.us")
        sender_id = _STRIP_SUFFIX.sub("", raw_from)  # preserva @lid

        media = p.get("media") or {}
        mimetype = str(media.get("mimetype") or "")
        is_audio = mimetype.startswith("audio/")
        media_url = media.get("url")

        text = p.get("body") or ""
        attachments: list = []
        audio_url = None
        if is_audio:
            audio_url = media_url  # pode ser None se o WAHA não baixou a mídia (hasMedia sem media)
            if audio_url:
                attachments.append(audio_url)
        elif p.get("hasMedia") and media_url:
            attachments.append(media_url)

        return ParsedMessage(
            channel=self.channel,
            provider=self.provider,
            location_id=location_id,
            sender_id=sender_id,
            provider_message_id=p.get("id"),
            text=text,
            is_audio=is_audio,
            audio_url=audio_url,
            attachments=attachments,
            is_group=is_group,
            from_me=bool(p.get("fromMe")),
            sender_name=p.get("notifyName") or "",
            message_type=payload.get("event") or "",
            event_kind="message",
            raw=payload,
        )

    # ── Outbound ──

    async def send_text(self, tenant, to: str, text: str, *, typing_delay: int = 0) -> OutboundResult:
        base, key, session = self._cfg(tenant)
        resp = await waha_service.send_text(base, key, session, self._chat_id(to), text)
        return OutboundResult(ok=resp is not None, provider_message_id=self._extract_message_id(resp))

    async def send_image(self, tenant, to: str, image_url: str, caption: str = "") -> OutboundResult:
        base, key, session = self._cfg(tenant)
        resp = await waha_service.send_image(base, key, session, self._chat_id(to), image_url, caption)
        return OutboundResult(ok=resp is not None, provider_message_id=self._extract_message_id(resp))

    async def send_audio(self, tenant, to: str, audio_data_url: str) -> OutboundResult:
        base, key, session = self._cfg(tenant)
        # O TTS entrega "data:audio/ogg;base64,<b64>"; o WAHA quer só o base64.
        b64 = audio_data_url.split(",", 1)[1] if "," in audio_data_url else audio_data_url
        resp = await waha_service.send_voice(base, key, session, self._chat_id(to), b64)
        return OutboundResult(ok=resp is not None, provider_message_id=self._extract_message_id(resp))

    async def register_webhook(self, tenant, public_base_url: str) -> bool:
        base, key, session = self._cfg(tenant)
        if not (base and session):
            return False
        webhook_url = f"{public_base_url.rstrip('/')}/webhook/whatsapp/{tenant.location_id}"
        hmac_key = getattr(settings, "waha_webhook_hmac_key", None) or None
        return await waha_service.set_session_webhook(
            base, key, session, webhook_url,
            events=["message", "message.ack", "session.status"],
            hmac_key=hmac_key,
        )
