"""
Abstração de canal (ChannelAdapter).

Cada canal/provedor (Z-API, WAHA, Telegram) implementa a mesma porta; a
orquestração compartilhada (parse -> dedup -> debounce -> IA -> envio) vive uma
vez só. Primeiro passo do plano abstrações-primeiro: normalizar o inbound num
`ParsedMessage` neutro.

Ver docs/wiki/decisoes/reestruturacao-abstracoes-primeiro.md
"""

from channels.base import (
    ChannelAdapter,
    ChannelCapabilities,
    OutboundResult,
    ParsedMessage,
)

__all__ = [
    "ChannelAdapter",
    "ChannelCapabilities",
    "OutboundResult",
    "ParsedMessage",
]
