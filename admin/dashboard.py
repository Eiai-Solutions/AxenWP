"""
Rotas do Admin Dashboard.
Fornece interface baseada em cookies + jinja2 para gerenciar tenants.
"""

from fastapi import APIRouter, Request, Form, Depends, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from typing import Optional
import os

from utils.logger import logger
from utils.config import settings
from auth.token_manager import token_manager
from services.zapi_service import zapi_service

router = APIRouter(prefix="/admin", tags=["Admin UI"])
templates = Jinja2Templates(directory="web/templates")

# Config da senha do Painel
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")


def verify_admin(admin_session: Optional[str] = Cookie(None)) -> bool:
    """Valida se o cookie da sessão corresponde à senha."""
    return admin_session == ADMIN_PASSWORD


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = None):
    return templates.TemplateResponse("login.html", {"request": request, "error": error})


@router.post("/login")
async def do_login(password: str = Form(...)):
    if password == ADMIN_PASSWORD:
        response = RedirectResponse(url="/admin/dashboard", status_code=303)
        response.set_cookie(key="admin_session", value=password, httponly=True, max_age=86400 * 30)
        return response
    
    return RedirectResponse(url="/admin/login?error=Senha incorreta", status_code=303)


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/admin/login", status_code=303)
    response.delete_cookie("admin_session")
    return response


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request, msg: str = None, err: str = None, authenticated: bool = Depends(verify_admin)):
    if not authenticated:
        return RedirectResponse(url="/admin/login", status_code=303)

    tenants = token_manager.get_all_tenants()
    # Ordenar por data de criação ou nome da empresa
    tenants.sort(key=lambda x: x.company_name)
    
    # Busca status online da Z-API para exibir no painel
    for t in tenants:
        setattr(t, "zapi_connection_status", "NOT_CONFIGURED")
        if t.zapi_instance_id and t.zapi_token:
            status_data = await zapi_service.get_status(t.zapi_instance_id, t.zapi_token, t.zapi_client_token)
            if status_data:
                # Retorno do z-api status é {"connected": true, "session": "CONNECTED"}
                t.zapi_connection_status = "CONNECTED" if status_data.get("connected") else "DISCONNECTED"

    return templates.TemplateResponse(
        "dashboard.html", 
        {
            "request": request, 
            "tenants": tenants, 
            "message": msg,
            "error_msg": err
        }
    )


@router.post("/tenant/{location_id}/zapi")
async def update_zapi_credentials(
    location_id: str,
    instance_id: str = Form(...),
    token: str = Form(...),
    client_token: str = Form(""),
    authenticated: bool = Depends(verify_admin)
):
    if not authenticated:
        return RedirectResponse(url="/admin/login", status_code=303)

    tenant_data = token_manager.get_tenant(location_id)
    if not tenant_data:
        return RedirectResponse(url="/admin/dashboard?err=Empresa não encontrada.", status_code=303)

    # Atualiza pelo TokenManager direto ao banco
    try:
        token_manager.update_zapi_credentials(
            location_id=tenant_data.location_id,
            instance_id=instance_id.strip(),
            token=token.strip(),
            client_token=client_token.strip()
        )
        return RedirectResponse(url="/admin/dashboard?msg=Credenciais do Z-API atualizadas!", status_code=303)
    except Exception as e:
        logger.error(f"Erro ao salvar z-api: {e}")
        return RedirectResponse(url="/admin/dashboard?err=Erro interno salvar.", status_code=303)


@router.get("/tenant/{location_id}/qrcode")
async def get_tenant_qrcode(location_id: str, authenticated: bool = Depends(verify_admin)):
    """Retorna o base64 do QR code da Z-API para reconexão via painel."""
    if not authenticated:
        return {"error": "Unauthorized"}
        
    tenant = token_manager.get_tenant(location_id)
    if not tenant or not tenant.zapi_instance_id:
        return {"error": "Tenant ou Z-API não configurados"}
        
    qr_data = await zapi_service.get_qr_code(
        tenant.zapi_instance_id, tenant.zapi_token, tenant.zapi_client_token
    )
    
    if qr_data and "value" in qr_data:
        return {"qrcode": qr_data["value"]}
        
    return {"error": "Não foi possível gerar QR Code"}


@router.post("/onboard")
async def onboard_new_company(
    company_name: str = Form(...),
    client_id: str = Form(...),
    client_secret: str = Form(...),
    authenticated: bool = Depends(verify_admin)
):
    """Guarda Client ID temporário e redireciona para OAuth do GHL"""
    if not authenticated:
        return RedirectResponse(url="/admin/login", status_code=303)

    # Redireciona para o OAuth interno, injetando parametros (A auth.py precisa lidar com eles agora)
    # Forma mais limpa: passarela para oauth/install repassando os secrets via redis/cookie.
    # Como não temos redis, vamos colocar no request path temporariamente
    from urllib.parse import urlencode
    params = {
        "company": company_name,
        "ci": client_id,
        "cs": client_secret,
        "ui_redirect": "1" # Sinaliza que originou do UI
    }
    url = f"/oauth/install?{urlencode(params)}"
    return RedirectResponse(url=url, status_code=303)

