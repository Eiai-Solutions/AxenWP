"""
Resolução de adapter de canal por tenant.

Ponto único de dispatch: dado um tenant, devolve o ChannelAdapter certo. Hoje só
para WhatsApp (Z-API default vs WAHA por flag). Telegram entra quando o pipeline
compartilhado absorver o telegram_receiver.
"""

from channels.whatsapp.waha import WAHAChannel
from channels.whatsapp.zapi import ZAPIChannel
from services.channel_policy import WAHA, ZAPI, active_whatsapp_provider


def resolve_whatsapp_adapter(tenant):
    """Seleciona o provedor de WhatsApp do tenant. Default 'zapi' (comportamento atual)."""
    provider = (getattr(tenant, "whatsapp_provider", None) or "zapi").lower()
    if provider == "waha":
        return WAHAChannel()
    return ZAPIChannel()


def resolve_send_adapter(tenant):
    """
    Adapter para ENVIAR, resolvido pelo provedor efetivamente ativo — ou None.

    Difere de `resolve_whatsapp_adapter` de propósito: aquela lê o flag cru e
    sempre devolve algum adapter; esta usa a mesma derivação da política de
    exclusividade (`active_whatsapp_provider`). Um tenant marcado "waha" mas sem
    sessão devolveria um adapter sem para onde enviar — aqui devolve None, e
    quem chama transforma isso num erro visível no CRM em vez de num silêncio.
    """
    ativo = active_whatsapp_provider(tenant)
    if ativo == WAHA:
        return WAHAChannel()
    if ativo == ZAPI:
        return ZAPIChannel()
    return None
