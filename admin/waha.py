"""
Gestão de conexões WhatsApp (WAHA) pelo painel.

O painel do WAHA fica invisível: criar/conectar/desconectar número acontece no
admin do AxenWP. Config do servidor WAHA é global (um servidor compartilhado);
cada tenant vira uma sessão nomeada pelo seu location_id.

Fluxo de conexão:
  connect -> cria/inicia sessão + registra webhook -> status vira SCAN_QR_CODE
  -> painel mostra o QR (get_qr) -> usuário escaneia -> status vira WORKING.
"""

from fastapi import APIRouter, Depends, Form, Response
from typing import Optional

from admin.dashboard import verify_admin
from auth.token_manager import token_manager
from data.database import SessionLocal
from data.models import SystemSettings, Tenant
from services.waha_service import waha_service
from utils.config import settings as app_settings
from utils.logger import logger

router = APIRouter(prefix="/admin/waha", tags=["WAHA Conexões"])


def _mask(secret: Optional[str]) -> str:
    if not secret:
        return ""
    if len(secret) <= 8:
        return "••••"
    return f"{secret[:4]}…{secret[-4:]}"


def _global_cfg() -> tuple[Optional[str], Optional[str]]:
    db = SessionLocal()
    try:
        s = db.query(SystemSettings).first()
        if not s:
            return None, None
        return (s.admin_waha_url or None), (s.admin_waha_api_key or None)
    finally:
        db.close()


def _resolve(tenant) -> tuple[Optional[str], Optional[str], str]:
    """Config efetiva do tenant: override por-tenant senão o global. Session = location_id."""
    g_url, g_key = _global_cfg()
    base = getattr(tenant, "waha_base_url", None) or g_url
    key = getattr(tenant, "waha_api_key", None) or g_key
    session = getattr(tenant, "waha_session", None) or tenant.location_id
    return base, key, session


# ── Config global do servidor WAHA ──

@router.get("/settings")
async def get_waha_settings(authenticated: bool = Depends(verify_admin)):
    if not authenticated:
        return {"error": "Unauthorized"}
    url, key = _global_cfg()
    return {"configured": bool(url and key), "url": url or "", "api_key_masked": _mask(key)}


@router.post("/settings")
async def save_waha_settings(
    admin_waha_url: str = Form(""),
    admin_waha_api_key: str = Form(""),
    authenticated: bool = Depends(verify_admin),
):
    if not authenticated:
        return {"error": "Unauthorized"}
    db = SessionLocal()
    try:
        s = db.query(SystemSettings).first()
        if not s:
            s = SystemSettings()
            db.add(s)
        s.admin_waha_url = admin_waha_url.strip().rstrip("/") or None
        # Só sobrescreve a key se veio preenchida (permite editar url sem reenviar a key).
        if admin_waha_api_key.strip():
            s.admin_waha_api_key = admin_waha_api_key.strip()
        db.commit()
        return {"success": True}
    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao salvar WAHA settings: {e}")
        return {"error": str(e)}
    finally:
        db.close()


@router.post("/settings/test")
async def test_waha_connection(authenticated: bool = Depends(verify_admin)):
    """Ping no servidor WAHA (lista sessões) para validar url+api_key."""
    if not authenticated:
        return {"error": "Unauthorized"}
    url, key = _global_cfg()
    if not (url and key):
        return {"ok": False, "error": "WAHA não configurado"}
    sessions = await waha_service.list_sessions(url, key)
    if sessions is None:
        return {"ok": False, "error": "Falha ao conectar (verifique url/api key)"}
    return {"ok": True, "sessions_count": len(sessions)}


# ── Conexão por tenant ──

@router.get("/tenant/{location_id}/status")
async def waha_status(location_id: str, authenticated: bool = Depends(verify_admin)):
    if not authenticated:
        return {"error": "Unauthorized"}
    tenant = token_manager.get_tenant(location_id)
    if not tenant:
        return {"error": "Tenant não encontrado"}
    base, key, session = _resolve(tenant)
    if not (base and key):
        return {"configured": False}
    info = await waha_service.get_session(base, key, session)
    status = (info or {}).get("status", "STOPPED") if info else "UNKNOWN"
    result = {"configured": True, "session": session, "status": status,
              "provider": getattr(tenant, "whatsapp_provider", "zapi")}
    if status == "WORKING":
        me = await waha_service.get_me(base, key, session)
        if me:
            result["me"] = me.get("id") or me.get("pushName") or me
    return result


@router.post("/tenant/{location_id}/connect")
async def waha_connect(location_id: str, authenticated: bool = Depends(verify_admin)):
    """Cria/inicia a sessão do tenant, registra o webhook e marca o tenant como WAHA."""
    if not authenticated:
        return {"error": "Unauthorized"}
    tenant = token_manager.get_tenant(location_id)
    if not tenant:
        return {"error": "Tenant não encontrado"}
    base, key, session = _resolve(tenant)
    if not (base and key):
        return {"error": "Configure o servidor WAHA (URL + API key) primeiro."}

    public_base = (app_settings.public_base_url or "").rstrip("/")
    webhook_url = f"{public_base}/webhook/whatsapp/{location_id}" if public_base else None
    hmac_key = getattr(app_settings, "waha_webhook_hmac_key", None) or None

    await waha_service.create_session(
        base, key, session, webhook_url=webhook_url,
        events=["message", "session.status"], hmac_key=hmac_key, start=True,
    )

    # Marca o tenant como WAHA e grava a config resolvida (denormaliza p/ o hot-path do envio).
    db = SessionLocal()
    try:
        t = db.query(Tenant).filter(Tenant.location_id == location_id).first()
        if t:
            t.whatsapp_provider = "waha"
            t.waha_base_url = base
            t.waha_api_key = key
            t.waha_session = session
            db.commit()
    finally:
        db.close()

    info = await waha_service.get_session(base, key, session)
    return {"success": True, "session": session, "status": (info or {}).get("status", "STARTING")}


@router.get("/tenant/{location_id}/qr")
async def waha_qr(location_id: str, authenticated: bool = Depends(verify_admin)):
    if not authenticated:
        return Response(status_code=401)
    tenant = token_manager.get_tenant(location_id)
    if not tenant:
        return Response(status_code=404)
    base, key, session = _resolve(tenant)
    if not (base and key):
        return Response(status_code=400)
    qr = await waha_service.get_qr(base, key, session)
    if not qr:
        return Response(status_code=404)
    content, content_type = qr
    return Response(content=content, media_type=content_type)


@router.post("/tenant/{location_id}/{action}")
async def waha_session_action(location_id: str, action: str, authenticated: bool = Depends(verify_admin)):
    """restart | logout | disconnect (logout+delete da sessão)."""
    if not authenticated:
        return {"error": "Unauthorized"}
    if action not in ("restart", "logout", "disconnect"):
        return {"error": "Ação inválida"}
    tenant = token_manager.get_tenant(location_id)
    if not tenant:
        return {"error": "Tenant não encontrado"}
    base, key, session = _resolve(tenant)
    if not (base and key):
        return {"error": "WAHA não configurado"}

    if action == "restart":
        ok = await waha_service.restart_session(base, key, session)
    else:  # logout | disconnect
        ok = await waha_service.logout_session(base, key, session)
        if action == "disconnect":
            await waha_service.delete_session(base, key, session)
    return {"success": ok}
