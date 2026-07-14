"""
Adapter Z-API (provedor de WhatsApp atual).

Neste primeiro passo implementa apenas `parse_inbound` — a normalização do
payload Z-API num `ParsedMessage`, extraída verbatim de
`webhooks/zapi_receiver.process_inbound_message`. Os métodos de envio
(delegando para `services.zapi_service`) entram junto com o pipeline
compartilhado no passo seguinte.
"""

from __future__ import annotations

from typing import Optional

from channels.base import ChannelCapabilities, ParsedMessage
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
