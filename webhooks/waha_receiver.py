"""
Webhook do WhatsApp self-host (WAHA).

O prefixo é `/webhook/whatsapp` e não `/webhook/waha` de propósito: é a URL que
fica gravada na sessão do provedor, e trocar de motor um dia não deve obrigar a
re-registrar webhook em todas as sessões.

O WAHA entrega TODOS os eventos na mesma URL, então a rota despacha por
`payload["event"]`: `message` vai para o pipeline de conversa, `session.status`
vira log/métrica, `message.ack` é reconhecido e ignorado por ora.
"""

import hashlib
import hmac
import json
from dataclasses import replace

from fastapi import APIRouter, BackgroundTasks, Path, Request

from auth.token_manager import token_manager
from channels.whatsapp.waha import WAHAChannel
from services.channel_policy import WAHA, active_whatsapp_provider
from services.inbound_pipeline import handle_inbound
from services.waha_service import waha_service
from utils import metrics
from utils.config import settings
from utils.limiter import limiter
from utils.logger import logger
from utils.validators import is_valid_location_id

router = APIRouter(prefix="/webhook/whatsapp", tags=["Webhooks WhatsApp (WAHA)"])

_channel = WAHAChannel()


def _hmac_ok(raw: bytes, headers) -> bool:
    """
    Só valida se houver chave configurada. Sessão registrada sem HMAC + validação
    obrigatória = todo inbound rejeitado; preferimos não inventar segurança que a
    sessão não tem.
    """
    key = (getattr(settings, "waha_webhook_hmac_key", "") or "").strip()
    if not key:
        return True
    enviado = headers.get("x-webhook-hmac") or ""
    esperado = hmac.new(key.encode(), raw, hashlib.sha512).hexdigest()
    return hmac.compare_digest(enviado, esperado)


@router.post("/{location_id}")
@limiter.limit("120/minute")
async def waha_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    location_id: str = Path(..., description="Location ID do tenant dono da sessão"),
):
    if not is_valid_location_id(location_id):
        logger.warning(f"[WAHA] location_id rejeitado por validação ({location_id!r})")
        metrics.inc("axenwp_webhook_rejected_total", labels={"channel": "whatsapp", "reason": "invalid_location_id"})
        return {"success": False, "error": "Invalid location_id"}

    raw = await request.body()
    if not _hmac_ok(raw, request.headers):
        logger.warning(f"[WAHA] HMAC inválido para {location_id}")
        metrics.inc("axenwp_webhook_rejected_total", labels={"channel": "whatsapp", "reason": "invalid_hmac"})
        return {"success": False, "error": "Invalid signature"}

    try:
        payload = json.loads(raw)
    except Exception:
        logger.error("[WAHA] Payload inválido (JSON).")
        metrics.inc("axenwp_webhook_rejected_total", labels={"channel": "whatsapp", "reason": "invalid_json"})
        return {"success": False, "error": "Invalid JSON"}

    evento = payload.get("event") or ""
    metrics.inc("axenwp_webhooks_received_total", labels={"channel": "whatsapp"})

    if evento == "message":
        background_tasks.add_task(process_waha_message, location_id, payload)
    elif evento == "session.status":
        status = (payload.get("payload") or {}).get("status", "?")
        logger.info(f"[WAHA] Sessão {location_id}: {status}")
        metrics.inc("axenwp_waha_session_status_total", labels={"status": str(status)})
    else:
        logger.debug(f"[WAHA] Evento ignorado: {evento or '(vazio)'}")

    # Sempre 200: o WAHA reentrega em resposta não-2xx.
    return {"success": True}


async def process_waha_message(location_id: str, payload: dict) -> None:
    tenant = token_manager.get_tenant(location_id)
    if not tenant:
        logger.error(f"[WAHA] Inbound abortado: tenant {location_id} não registrado.")
        return

    if not getattr(tenant, "is_active", True):
        logger.info(f"[WAHA] Inbound abortado: automação desativada para {location_id}.")
        return

    # Espelho da guarda que o receiver da Z-API tem: só atende quem realmente
    # está no WAHA. Sessão órfã de uma instância que voltou para a Z-API não
    # pode continuar conversando.
    if active_whatsapp_provider(tenant) != WAHA:
        logger.info(f"[CHANNEL] Inbound WAHA ignorado: {location_id} não usa WAHA como provedor.")
        metrics.inc("axenwp_webhook_rejected_total", labels={"channel": "whatsapp", "reason": "provider_inactive"})
        return

    pm = _channel.parse_inbound(location_id, payload)
    if not pm:
        return

    # Fallback de identidade: o parse já resolve @lid -> telefone quando o payload
    # traz Info.SenderAlt (caso normal, sem I/O). Só quando não veio é que pagamos
    # o lookup no WAHA — e apenas para mensagem que vai ser processada de fato,
    # nunca para eco nosso ou grupo, que o pipeline descartaria em seguida.
    if "@lid" in pm.sender_id and not pm.from_me and not pm.is_group:
        base, key, session = _channel._cfg(tenant)
        fone = await waha_service.resolve_lid(base, key, session, pm.sender_id)
        if fone:
            pm = replace(pm, sender_id=fone, sender_lid=pm.sender_id)
        else:
            logger.warning(
                f"[WAHA] LID {pm.sender_id} não resolvido — contato entrará sem telefone."
            )

    logger.info(
        f"[WAHA] inbound location={location_id} de={pm.sender_id} "
        f"lid={pm.sender_lid or '-'} "
        f"audio={pm.is_audio} anexos={len(pm.attachments)} id={pm.provider_message_id}"
    )

    await handle_inbound(_channel, tenant, pm)
