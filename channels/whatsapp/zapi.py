"""
Adapter Z-API (provedor de WhatsApp atual).

Implementa `parse_inbound` (normalização do payload Z-API num `ParsedMessage`,
extraída verbatim de `webhooks/zapi_receiver.process_inbound_message`) e os
métodos de envio, que delegam para `services.zapi_service` sem alterar um
argumento sequer — o adapter é uma fachada, não uma reimplementação.
"""

from __future__ import annotations

from typing import Optional

from channels.base import ChannelCapabilities, OutboundResult, ParsedMessage
from services.zapi_service import zapi_service
from utils.logger import logger


class ZAPIChannel:
    channel = "whatsapp"
    provider = "zapi"
    capabilities = ChannelCapabilities(
        supports_audio_ptt=True,
        supports_typing_delay=True,
        outbound_media_accepts_data_url=True,
        provider_reechoes_own_msgs=False,  # Z-API já filtra fromMe no inbound
    )

    def parse_inbound(
        self, location_id: str, payload: dict, headers: Optional[dict] = None
    ) -> ParsedMessage:
        """Normaliza o payload Z-API. Mantém byte-a-byte a lógica legada."""
        phone = payload.get("phone", "")
        message_type = payload.get("type", "")
        is_group = payload.get("isGroup", False)
        from_me = payload.get("fromMe", False)
        # Expressão preservada verbatim do receiver (precedência intencional).
        msg_id = payload.get("messageId") or payload.get("ids", [None])[0] if payload.get("ids") else payload.get("messageId")

        content_message = "Mensagem recebida do WhatsApp"
        attachments: list = []
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

        sender_name = payload.get("senderName") or payload.get("participantName") or ""

        return ParsedMessage(
            channel=self.channel,
            provider=self.provider,
            location_id=location_id,
            sender_id=phone,
            provider_message_id=msg_id,
            text=content_message,
            is_audio=is_audio,
            audio_url=audio_url,
            attachments=attachments,
            is_group=is_group,
            from_me=from_me,
            sender_name=sender_name,
            message_type=message_type,
            event_kind="message",
            raw=payload,
        )

    # ── Outbound ──
    #
    # Delegação fina para `services.zapi_service`, preservando EXATAMENTE os
    # argumentos que `webhooks/ghl_provider` e o pipeline já passavam — inclusive
    # `client_token`, `delay_typing` e `record_audio`. Qualquer divergência aqui
    # apareceria como regressão silenciosa nos tenants que rodam em Z-API.

    def credentials_ok(self, tenant) -> bool:
        return bool(getattr(tenant, "zapi_instance_id", None) and getattr(tenant, "zapi_token", None))

    def _creds(self, tenant) -> tuple[str, str, str]:
        return (
            tenant.zapi_instance_id,
            tenant.zapi_token,
            getattr(tenant, "zapi_client_token", None) or "",
        )

    @staticmethod
    def _result(resp: Optional[dict]) -> OutboundResult:
        msg_id = resp.get("zapiMessageId") if isinstance(resp, dict) else None
        return OutboundResult(ok=bool(resp), provider_message_id=msg_id)

    async def send_text(self, tenant, to: str, text: str, *, typing_delay: int = 0) -> OutboundResult:
        instance_id, token, client_token = self._creds(tenant)
        resp = await zapi_service.send_text(
            instance_id=instance_id, token=token, phone=to, message=text,
            client_token=client_token, delay_typing=typing_delay,
        )
        return self._result(resp)

    async def send_image(self, tenant, to: str, image_url: str, caption: str = "") -> OutboundResult:
        instance_id, token, client_token = self._creds(tenant)
        resp = await zapi_service.send_image(
            instance_id=instance_id, token=token, phone=to, image_url=image_url,
            caption=caption, client_token=client_token,
        )
        return self._result(resp)

    async def send_audio(self, tenant, to: str, audio_data_url: str) -> OutboundResult:
        instance_id, token, client_token = self._creds(tenant)
        resp = await zapi_service.send_audio(
            instance_id=instance_id, token=token, phone=to, audio_url=audio_data_url,
            client_token=client_token, record_audio=True,
        )
        return self._result(resp)

    async def send_document(self, tenant, to: str, document_url: str, filename: str = "document") -> OutboundResult:
        instance_id, token, client_token = self._creds(tenant)
        resp = await zapi_service.send_document(
            instance_id=instance_id, token=token, phone=to, document_url=document_url,
            filename=filename, client_token=client_token,
        )
        return self._result(resp)
