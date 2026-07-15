"""
Resolução de adapter de canal por tenant.

Ponto único de dispatch: dado um tenant, devolve o ChannelAdapter certo. Hoje só
para WhatsApp (Z-API default vs WAHA por flag). Telegram entra quando o pipeline
compartilhado absorver o telegram_receiver.
"""

from channels.whatsapp.waha import WAHAChannel
from channels.whatsapp.zapi import ZAPIChannel


def resolve_whatsapp_adapter(tenant):
    """Seleciona o provedor de WhatsApp do tenant. Default 'zapi' (comportamento atual)."""
    provider = (getattr(tenant, "whatsapp_provider", None) or "zapi").lower()
    if provider == "waha":
        return WAHAChannel()
    return ZAPIChannel()
