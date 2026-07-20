"""
Adapter WAHA (WhatsApp HTTP API self-host).

Implementa a porta ChannelAdapter contra a API REST do WAHA, ligada ao inbound
(`webhooks/waha_receiver`) e ao envio (`webhooks/ghl_provider` e o pipeline).

Diferenças-chave vs Z-API (ver ChannelCapabilities):
- WAHA REENTREGA as próprias mensagens (fromMe) -> dedup por provider_message_id
  é obrigatório (o pipeline compartilhado cuida disso).
- Áudio: o TTS entrega data-url (vai como base64 em file.data), o anexo do CRM
  entrega URL http (vai como file.url, com o WAHA transcodificando).
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
        """
        Destinatário no formato que o WAHA espera.

        A Z-API tolerava telefone formatado porque descartava tudo que não é
        dígito; o WAHA aceita o POST com um chatId torto e a mensagem
        simplesmente não sai — falha silenciosa. Por isso normalizamos aqui:
        "+55 47 99720-4869" → "5547997204869@c.us".
        """
        alvo = (to or "").strip()
        if "@" in alvo:
            if alvo.endswith("@lid"):
                # Identidade de lead de anúncio: não é um chatId comum. Deixamos
                # passar (o GOWS resolve alguns casos) mas registramos, porque é
                # candidato número 1 quando "enviou e não chegou".
                logger.warning(f"[WAHA] Enviando para identidade @lid ({alvo}); entrega não é garantida.")
            return alvo
        digitos = re.sub(r"\D", "", alvo)
        return f"{digitos or alvo}@c.us"

    @staticmethod
    def _extract_message_id(resp: Optional[dict]) -> Optional[str]:
        """
        Id da mensagem enviada, sempre string — ou None.

        O id do WAHA muda de forma por engine: string direta, `key.id`, ou um
        objeto `{"_serialized": "..."}`. Devolver o objeto cru faria o
        `save_message_mapping` gravar um dict na chave primária, e o status de
        entrega dessa mensagem nunca mais casaria.
        """
        if not isinstance(resp, dict):
            return None
        bruto = (
            resp.get("id")
            or (resp.get("key") or {}).get("id")
            or (resp.get("_data") or {}).get("id")
        )
        if isinstance(bruto, dict):
            bruto = bruto.get("_serialized") or bruto.get("id")
        return bruto if isinstance(bruto, str) and bruto.strip() else None

    def _result(self, resp: Optional[dict]) -> OutboundResult:
        """
        Resposta do WAHA -> OutboundResult.

        `resp is not None` significa que o servidor aceitou (2xx). Mantemos isso
        como sucesso mesmo sem id: marcar 'failed' uma mensagem que de fato saiu
        levaria o operador a reenviar, e o cliente receberia duas vezes. Mas sem
        id o vínculo com o CRM se perde e o status congela — por isso o aviso.
        """
        msg_id = self._extract_message_id(resp)
        if resp is not None and not msg_id:
            logger.warning(
                "[WAHA] Envio aceito sem id de mensagem — status de entrega não subirá para o CRM. "
                f"Resposta: {str(resp)[:200]}"
            )
        return OutboundResult(ok=resp is not None, provider_message_id=msg_id)

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
        return self._result(resp)

    async def send_image(self, tenant, to: str, image_url: str, caption: str = "") -> OutboundResult:
        base, key, session = self._cfg(tenant)
        resp = await waha_service.send_image(base, key, session, self._chat_id(to), image_url, caption)
        return self._result(resp)

    async def send_audio(self, tenant, to: str, audio_data_url: str) -> OutboundResult:
        """
        Aceita as duas origens de áudio do sistema, que têm formatos diferentes:
        o TTS entrega "data:audio/ogg;base64,<b64>" e o anexo do CRM entrega uma
        URL http. Tratar a URL como base64 (o que o split por vírgula fazia)
        colocava a própria URL dentro de file.data — o áudio nunca saía, e se a
        URL assinada tivesse uma vírgula ainda ia cortada ao meio.
        """
        base, key, session = self._cfg(tenant)
        alvo = self._chat_id(to)
        if audio_data_url.startswith("http"):
            resp = await waha_service.send_voice(base, key, session, alvo, audio_url=audio_data_url)
        else:
            b64 = audio_data_url.split(",", 1)[1] if "," in audio_data_url else audio_data_url
            resp = await waha_service.send_voice(base, key, session, alvo, b64)
        return self._result(resp)

    async def send_document(self, tenant, to: str, document_url: str, filename: str = "documento") -> OutboundResult:
        base, key, session = self._cfg(tenant)
        # O WAHA baixa a URL do lado dele — anexo do CRM precisa ser público.
        resp = await waha_service.send_file(base, key, session, self._chat_id(to), document_url, filename)
        return self._result(resp)

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
