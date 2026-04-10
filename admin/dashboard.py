"""
Rotas do Admin Dashboard.
Fornece interface baseada em cookies + jinja2 para gerenciar tenants.
"""

import uuid

from fastapi import APIRouter, Request, Form, Depends, Cookie
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from typing import Optional
import hmac
import hashlib

from utils.logger import logger
from utils.config import settings as app_settings
from auth.token_manager import token_manager
from services.zapi_service import zapi_service

router = APIRouter(prefix="/admin", tags=["Admin UI"])
templates = Jinja2Templates(directory="web/templates")

# Config da senha do Painel — lida via settings (env var ADMIN_PASSWORD)
_WEAK_PASSWORDS = {"admin123", "admin", "password", "123456", ""}


def _get_admin_password() -> str:
    """Retorna a senha admin configurada. Falha se nao definida em producao."""
    pw = app_settings.admin_password
    if not pw and app_settings.debug:
        logger.warning("ADMIN_PASSWORD nao definido — usando fallback 'admin123' (modo DEBUG).")
        return "admin123"
    if not pw:
        raise RuntimeError(
            "ADMIN_PASSWORD nao configurado. "
            "Defina a variavel de ambiente ADMIN_PASSWORD antes de iniciar em producao."
        )
    if pw in _WEAK_PASSWORDS:
        logger.warning(
            "ADMIN_PASSWORD e uma senha fraca conhecida. "
            "Troque por uma senha forte em producao."
        )
    return pw


def _make_session_token(password: str) -> str:
    """Deriva um token de sessão seguro a partir da senha (nunca expõe a senha no cookie)."""
    return hmac.new(password.encode(), b"axenwp-admin-session", hashlib.sha256).hexdigest()


def verify_admin(admin_session: Optional[str] = Cookie(None)) -> bool:
    """Valida se o cookie da sessão é um token HMAC válido."""
    expected = _make_session_token(_get_admin_password())
    return hmac.compare_digest(admin_session or "", expected)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = None):
    return templates.TemplateResponse("login.html", {"request": request, "error": error})


@router.post("/login")
async def do_login(password: str = Form(...)):
    if password == _get_admin_password():
        response = RedirectResponse(url="/admin/dashboard", status_code=303)
        response.set_cookie(
            key="admin_session",
            value=_make_session_token(password),
            httponly=True,
            samesite="lax",
            max_age=86400 * 30,
        )
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
    
    # Busca os Agentes de IA e System Settings
    from data.database import SessionLocal
    from data.models import AIAgent, SystemSettings
    db = SessionLocal()
    agent_map = {}
    system_settings = None
    try:
        # Filtra agentes do canal WhatsApp para a linha principal da tabela.
        # Outros canais (Instagram, etc) sao gerenciados via modal multi-canal.
        agents = db.query(AIAgent).filter(AIAgent.channel == "whatsapp").all()
        for a in agents:
            agent_map[a.location_id] = a
            
        settings = db.query(SystemSettings).first()
        if settings:
            system_settings = {
                "admin_openrouter_key": settings.admin_openrouter_key,
                "admin_openrouter_model": settings.admin_openrouter_model
            }
        else:
            system_settings = {
                "admin_openrouter_key": "",
                "admin_openrouter_model": "openai/gpt-4o"
            }
    except Exception as e:
        logger.error(f"Erro ao buscar AI Agents/Settings: {e}")
    finally:
        db.close()
    
    # Converter Tenant e Agent para dicts locais para garantir que as propriedades
    # não se percam quando a sessão do SQLAlchemy fechar.
    tenants_list = []
    
    # Busca status online da Z-API para exibir no painel
    for t in tenants:
        t_dict = {
            "location_id": t.location_id,
            "company_name": t.company_name,
            "is_active": t.is_active,
            "mode": getattr(t, "mode", "ghl") or "ghl",
            "is_token_expired": t.is_token_expired if getattr(t, "mode", "ghl") == "ghl" else False,
            "zapi_instance_id": t.zapi_instance_id,
            "zapi_token": t.zapi_token,
            "zapi_client_token": t.zapi_client_token,
            "client_id": t.client_id,
        }
        
        agent = agent_map.get(t.location_id)
        if agent:
            t_dict["ai_agent_data"] = {
                "name": agent.name,
                "prompt": agent.prompt,
                "model": agent.model,
                "api_key": agent.api_key,
                "elevenlabs_api_key": agent.elevenlabs_api_key,
                "elevenlabs_voice_id": agent.elevenlabs_voice_id,
                "elevenlabs_speed": float(agent.elevenlabs_speed) if agent.elevenlabs_speed is not None else 1.0,
                "elevenlabs_stability": float(agent.elevenlabs_stability) if agent.elevenlabs_stability is not None else 0.5,
                "elevenlabs_similarity": float(agent.elevenlabs_similarity) if agent.elevenlabs_similarity is not None else 0.75,
                "groq_api_key": agent.groq_api_key,
                "is_active": agent.is_active,
                "debounce_seconds": float(agent.debounce_seconds) if agent.debounce_seconds is not None else 1.5,
                "qualification_enabled": bool(getattr(agent, 'qualification_enabled', False)),
                "qualification_pipeline_id": getattr(agent, 'qualification_pipeline_id', '') or '',
                "qualification_stage_id": getattr(agent, 'qualification_stage_id', '') or '',
                "qualification_fields": getattr(agent, 'qualification_fields', None) or [],
                "qualification_summary_prompt": getattr(agent, 'qualification_summary_prompt', '') or '',
            }
        else:
            t_dict["ai_agent_data"] = None

        t_dict["zapi_connection_status"] = "NOT_CONFIGURED"
        if t.zapi_instance_id and t.zapi_token:
            status_data = await zapi_service.get_status(t.zapi_instance_id, t.zapi_token, t.zapi_client_token)
            if status_data:
                t_dict["zapi_connection_status"] = "CONNECTED" if status_data.get("connected") else "DISCONNECTED"
                
        tenants_list.append(t_dict)

    return templates.TemplateResponse(
        "dashboard.html", 
        {
            "request": request, 
            "tenants": tenants_list, 
            "message": msg,
            "error_msg": err,
            "system_settings": system_settings
        }
    )


@router.post("/tenant/{location_id}/toggle")
async def toggle_tenant_automation(
    location_id: str,
    is_active: bool = Form(...),
    authenticated: bool = Depends(verify_admin)
):
    if not authenticated:
        return RedirectResponse(url="/admin/login", status_code=303)

    try:
        token_manager.toggle_active_status(location_id, is_active)
        status_msg = "ativada" if is_active else "desativada"
        return RedirectResponse(url=f"/admin/dashboard?msg=Automação da instância foi {status_msg}.", status_code=303)
    except Exception as e:
        logger.error(f"Erro ao alternar status da instância: {e}")
        return RedirectResponse(url="/admin/dashboard?err=Erro interno ao tentar alternar o status.", status_code=303)


@router.post("/tenant/{location_id}/generate-form-link")
async def generate_form_link(
    request: Request,
    location_id: str,
    authenticated: bool = Depends(verify_admin)
):
    """Gera ou retorna o link do formulário de onboarding para um tenant."""
    if not authenticated:
        return JSONResponse({"success": False, "error": "Não autenticado."}, status_code=401)

    from data.database import SessionLocal
    from data.models import Tenant
    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter(Tenant.location_id == location_id).first()
        if not tenant:
            return JSONResponse({"success": False, "error": "Tenant não encontrado."}, status_code=404)

        # Gera token se ainda não tem
        if not tenant.form_token:
            tenant.form_token = uuid.uuid4().hex
            db.commit()

        base_url = str(request.base_url).rstrip("/")
        form_url = f"{base_url}/form/{tenant.form_token}"

        return JSONResponse({"success": True, "url": form_url})
    finally:
        db.close()


@router.post("/tenant/{location_id}/delete")
async def delete_tenant(
    location_id: str,
    authenticated: bool = Depends(verify_admin)
):
    if not authenticated:
        return RedirectResponse(url="/admin/login", status_code=303)

    tenant = token_manager.get_tenant(location_id)
    if not tenant:
        return RedirectResponse(url="/admin/dashboard?err=Empresa não encontrada.", status_code=303)

    company_name = tenant.company_name
    success = token_manager.delete_tenant(location_id)
    if success:
        return RedirectResponse(url=f"/admin/dashboard?msg=Instância '{company_name}' removida com sucesso.", status_code=303)
    return RedirectResponse(url="/admin/dashboard?err=Erro ao remover instância.", status_code=303)


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


@router.post("/system/save")
async def save_system_settings(
    admin_openrouter_key: str = Form(""),
    admin_openrouter_model: str = Form("openai/gpt-4o"),
    authenticated: bool = Depends(verify_admin)
):
    """Salva configurações globais do Admin."""
    if not authenticated:
        return RedirectResponse(url="/admin/login", status_code=303)

    from data.database import SessionLocal
    from data.models import SystemSettings
    db = SessionLocal()
    try:
        settings = db.query(SystemSettings).first()
        if not settings:
            settings = SystemSettings()
            db.add(settings)
        
        settings.admin_openrouter_key = admin_openrouter_key.strip()
        settings.admin_openrouter_model = admin_openrouter_model.strip()
        
        db.commit()
        return RedirectResponse(url="/admin/dashboard?msg=Configurações globais salvas com sucesso.", status_code=303)
    except Exception as e:
        logger.error(f"Erro ao salvar SystemSettings: {e}")
        db.rollback()
        return RedirectResponse(url=f"/admin/dashboard?err=Erro ao salvar configurações globais: {str(e)}", status_code=303)
    finally:
        db.close()



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


@router.get("/api/usage/{location_id}")
async def get_usage_data(
    location_id: str,
    period: str = "30d",
    authenticated: bool = Depends(verify_admin)
):
    """Retorna dados de uso/custo de um tenant agrupados por serviço e por dia."""
    if not authenticated:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    from datetime import datetime, timedelta, timezone
    from sqlalchemy import func, cast, Date
    from data.database import SessionLocal
    from data.models import UsageLog

    days = int(period.replace("d", "")) if period.endswith("d") else 30
    since = datetime.now(timezone.utc) - timedelta(days=days)

    db = SessionLocal()
    try:
        logs = db.query(UsageLog).filter(
            UsageLog.location_id == location_id,
            UsageLog.created_at >= since,
        ).all()

        # Resumo por serviço
        by_service = {}
        daily = {}
        for log in logs:
            svc = log.service
            if svc not in by_service:
                by_service[svc] = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "characters": 0, "cost_usd": 0.0}
            by_service[svc]["calls"] += 1
            by_service[svc]["input_tokens"] += log.input_tokens or 0
            by_service[svc]["output_tokens"] += log.output_tokens or 0
            by_service[svc]["characters"] += log.characters or 0
            by_service[svc]["cost_usd"] += log.cost_usd or 0.0

            day_key = log.created_at.strftime("%Y-%m-%d") if log.created_at else "unknown"
            if day_key not in daily:
                daily[day_key] = {"calls": 0, "cost_usd": 0.0}
            daily[day_key]["calls"] += 1
            daily[day_key]["cost_usd"] += log.cost_usd or 0.0

        total_calls = sum(s["calls"] for s in by_service.values())
        total_cost = sum(s["cost_usd"] for s in by_service.values())

        return JSONResponse({
            "location_id": location_id,
            "period_days": days,
            "total_calls": total_calls,
            "total_cost_usd": round(total_cost, 4),
            "by_service": by_service,
            "daily": dict(sorted(daily.items())),
        })
    finally:
        db.close()


@router.post("/onboard-whatsapp")
async def onboard_whatsapp_only(
    company_name: str = Form(...),
    zapi_instance_id: str = Form(...),
    zapi_token: str = Form(...),
    zapi_client_token: str = Form(""),
    authenticated: bool = Depends(verify_admin)
):
    """Cria um tenant WhatsApp-only sem integração com CRM."""
    if not authenticated:
        return RedirectResponse(url="/admin/login", status_code=303)

    try:
        tenant = token_manager.create_whatsapp_tenant(
            company_name=company_name.strip(),
            zapi_instance_id=zapi_instance_id.strip(),
            zapi_token=zapi_token.strip(),
            zapi_client_token=zapi_client_token.strip(),
        )
        return RedirectResponse(
            url=f"/admin/dashboard?msg=Instância WhatsApp '{tenant.company_name}' criada com sucesso!",
            status_code=303
        )
    except Exception as e:
        logger.error(f"Erro ao criar tenant WhatsApp-only: {e}")
        return RedirectResponse(url=f"/admin/dashboard?err=Erro ao criar instância: {str(e)}", status_code=303)

