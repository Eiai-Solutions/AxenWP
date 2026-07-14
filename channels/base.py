"""
Contratos da porta de canal.

`ParsedMessage` é a representação neutra de uma mensagem inbound — o pipeline
compartilhado nunca precisa saber se veio da Z-API, do WAHA ou do Telegram.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol


@dataclass
class ParsedMessage:
    """Mensagem inbound normalizada, agnóstica de provedor."""
    channel: str                 # "whatsapp" | "telegram"
    provider: str                # "zapi" | "waha" | "telegram"
    location_id: str
    # Identidade crua do remetente como o provedor envia. Na Z-API é o `phone`,
    # que pode conter sufixo `@lid` (leads de anúncio). A normalização
    # (`split("@")`) acontece adiante, como já é feito hoje em ai_service.
    sender_id: str
    provider_message_id: Optional[str]
    text: str
    is_audio: bool = False
    audio_url: Optional[str] = None
    attachments: list = field(default_factory=list)
    is_group: bool = False
    from_me: bool = False
    sender_name: str = ""
    message_type: str = ""       # tipo bruto do provedor (ex.: "ReceivedCallback")
    event_kind: str = "message"  # "message" | "status" | "ignore"
    raw: dict = field(default_factory=dict)


@dataclass
class OutboundResult:
    ok: bool
    provider_message_id: Optional[str] = None
    error: Optional[str] = None


@dataclass
class ChannelCapabilities:
    supports_audio_ptt: bool = True
    supports_typing_delay: bool = True
    # Z-API aceita data-url direto no áudio; WAHA precisa de base64 em file.data.
    outbound_media_accepts_data_url: bool = True
    # True => o provedor reentrega as próprias mensagens (fromMe) e dedup por
    # provider_message_id é obrigatório. Z-API já filtra fromMe (False); WAHA reentrega.
    provider_reechoes_own_msgs: bool = False


class ChannelAdapter(Protocol):
    """Porta que cada canal/provedor implementa (structural/Protocol)."""
    channel: str
    provider: str
    capabilities: ChannelCapabilities

    def parse_inbound(
        self, location_id: str, payload: dict, headers: Optional[dict] = None
    ) -> Optional[ParsedMessage]:
        ...

    async def send_text(
        self, tenant, to: str, text: str, *, typing_delay: int = 0
    ) -> OutboundResult:
        ...

    async def send_image(
        self, tenant, to: str, image_url: str, caption: str = ""
    ) -> OutboundResult:
        ...

    async def send_audio(self, tenant, to: str, audio_data_url: str) -> OutboundResult:
        ...

    def credentials_ok(self, tenant) -> bool:
        ...

    async def register_webhook(self, tenant, public_base_url: str) -> bool:
        ...
