"""
Estado ao vivo dos canais de uma instância — fonte única da aba CANAIS.

Antes a UI derivava o provedor de um atributo HTML (`data-provider`) gravado no
carregamento da página: quem conectasse um número pelo modal continuava vendo
"nenhum canal configurado" até dar F5, porque o atributo era um retrato do
passado. Aqui o painel pergunta e o servidor responde com o que está valendo
AGORA — inclusive consultando o servidor WAHA, onde de fato mora o estado da
sessão.

A mesma resposta carrega o estado de bloqueio de cada provedor, para a UI não
precisar reimplementar (e divergir de) a regra de exclusividade do backend.
"""

from fastapi import APIRouter, Depends

from admin.dashboard import verify_admin
from admin.waha import _resolve
from auth.token_manager import token_manager
from services.channel_policy import (
    WAHA,
    ZAPI,
    active_whatsapp_provider,
    provider_label,
)
from services.waha_service import waha_service
from services.zapi_service import zapi_service
from utils.logger import logger
from utils.validators import is_valid_location_id

router = APIRouter(prefix="/admin/instance", tags=["Canais da instância"])


def _row(state: str, title: str, detail: str) -> dict:
    """Uma faixa de status: 'ok' (verde) · 'pending' (ocre) · 'down' (vermelho)."""
    return {"state": state, "title": title, "detail": detail}


def _me_label(me) -> str:
    """Número conectado, como o WAHA devolve (pode vir dict ou string)."""
    if not me:
        return ""
    if isinstance(me, str):
        return me
    if isinstance(me, dict):
        return me.get("id") or me.get("pushName") or ""
    return str(me)


async def _whatsapp_waha(tenant) -> dict:
    base, key, session = _resolve(tenant)
    if not (base and key):
        # Sessão gravada mas servidor global sumiu: é falha de configuração do
        # admin, não "canal inexistente" — dizer isso em vez de esconder.
        return _row("down", "WhatsApp indisponível", "via WAHA · servidor não configurado")

    info = await waha_service.get_session(base, key, session)
    status = (info or {}).get("status") if info else None

    if status == "WORKING":
        me = await waha_service.get_me(base, key, session)
        numero = _me_label(me)
        return _row("ok", "WhatsApp conectado", "via WAHA" + (f" · {numero}" if numero else ""))
    if status == "SCAN_QR_CODE":
        return _row("pending", "WhatsApp aguardando leitura do QR", "via WAHA")
    if status == "STARTING":
        return _row("pending", "WhatsApp iniciando a sessão", "via WAHA")
    if status is None:
        # get_session devolve None quando o servidor não responde 200 (inclusive
        # 404 de sessão que sumiu do WAHA por fora do painel).
        return _row("down", "WhatsApp sem resposta do servidor", "via WAHA")
    return _row("down", "WhatsApp desconectado", f"via WAHA · {status}")


async def _whatsapp_zapi(tenant) -> dict:
    status = await zapi_service.get_status(
        tenant.zapi_instance_id, tenant.zapi_token, getattr(tenant, "zapi_client_token", None) or ""
    )
    if status is None:
        return _row("down", "WhatsApp sem resposta do provedor", "via Z-API")
    if status.get("connected"):
        return _row("ok", "WhatsApp conectado", "via Z-API")
    return _row("down", "WhatsApp desconectado", "via Z-API")


@router.get("/{location_id}/channels")
async def instance_channels(location_id: str, authenticated: bool = Depends(verify_admin)):
    """Estado vivo dos canais + quais provedores estão livres/bloqueados."""
    if not authenticated:
        return {"error": "Unauthorized"}
    if not is_valid_location_id(location_id):
        return {"error": "location_id inválido"}

    tenant = token_manager.get_tenant(location_id)
    if not tenant:
        return {"error": "Instância não encontrada"}

    active = active_whatsapp_provider(tenant)

    whatsapp = None
    if active == WAHA:
        try:
            whatsapp = await _whatsapp_waha(tenant)
        except Exception as e:
            logger.error(f"[CHANNELS] Falha ao consultar WAHA de {location_id}: {e}")
            whatsapp = _row("down", "WhatsApp sem resposta do servidor", "via WAHA")
    elif active == ZAPI:
        try:
            whatsapp = await _whatsapp_zapi(tenant)
        except Exception as e:
            logger.error(f"[CHANNELS] Falha ao consultar Z-API de {location_id}: {e}")
            whatsapp = _row("down", "WhatsApp sem resposta do provedor", "via Z-API")

    telegram = None
    if getattr(tenant, "telegram_bot_token", None):
        user = getattr(tenant, "telegram_bot_username", None)
        telegram = _row("ok", "Telegram conectado", f"@{user}" if user else "")

    # Estado de cada card de provedor. 'active' = é quem manda · 'locked' = não
    # pode ser configurado agora · 'swap' = pode, mas substitui o atual.
    if active == WAHA:
        providers = {
            "waha": {"state": "active", "hint": "Conectado — clique para ver o número ou desconectar"},
            "zapi": {
                "state": "locked",
                "hint": f"Indisponível — WhatsApp já conectado via {provider_label(WAHA)}",
                "tooltip": "Bloqueado: um provedor de WhatsApp por instância — desconecte o WAHA primeiro",
            },
        }
    elif active == ZAPI:
        providers = {
            "waha": {
                "state": "swap",
                "hint": "Substitui a Z-API desta instância",
                "tooltip": "Trocar de provedor: conectar pelo WAHA desativa a Z-API",
            },
            "zapi": {"state": "active", "hint": "Configurado — clique para editar credenciais"},
        }
    else:
        providers = {"waha": {"state": "free"}, "zapi": {"state": "free"}}

    return {
        "provider": active,
        "whatsapp": whatsapp,
        "telegram": telegram,
        "providers": providers,
    }
