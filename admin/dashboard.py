"""
Rotas do Admin Dashboard.
Fornece interface baseada em cookies + jinja2 para gerenciar tenants.
"""

import uuid

from fastapi import APIRouter, Request, Form, Depends, Cookie, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from typing import Optional
import asyncio

from utils.logger import logger
from utils.config import settings as app_settings
from utils.limiter import limiter
from auth.token_manager import token_manager
from services.channel_policy import ZAPI, active_whatsapp_provider
from services.zapi_service import zapi_service
from services.telegram_service import telegram_service

# Duas portas de propósito:
# `router_publico` só tem login/logout. Todo o resto vive em `router`, que já nasce
# com `require_admin` — rota nova é fechada por CONSTRUÇÃO, não por lembrar de
# checar. O padrão antigo (`verify_admin` devolvendo bool + `if not authenticated`
# em cada rota) foi o que deixou 25 rotas abertas em admin/ai_agent.py.
router_publico = APIRouter(prefix="/admin", tags=["Admin UI"])
router = APIRouter(prefix="/admin", tags=["Admin UI"])
templates = Jinja2Templates(directory="web/templates")

def _mask_app_id(client_id: str) -> str:
    """
    Identifica o app sem expor a chave inteira.

    O client id do GHL vem como "<appId>-<sufixoDaChave>": mostramos o começo do
    appId e o sufixo, que é justamente o que distingue uma client key da outra
    quando o mesmo app tem várias.
    """
    if not client_id:
        return ""
    app_id, _, suffix = client_id.partition("-")
    head = app_id[:8] if app_id else client_id[:8]
    return f"{head}…-{suffix}" if suffix else f"{head}…"


def current_principal(admin_session: Optional[str] = Cookie(None)):
    """O principal da request (username + papel + tenants alcançáveis), ou None."""
    from services.admin_auth import resolve_principal

    return resolve_principal(admin_session)


def verify_admin(admin_session: Optional[str] = Cookie(None)) -> bool:
    """
    Só é verdadeiro para OPERADOR — todo `/admin` é área da equipe.

    Este é o ponto que impede o painel do cliente de virar vazamento: no instante
    em que existir um `AdminUser` com role='client', ele passaria nas 86 rotas de
    `/admin` se a checagem continuasse sendo apenas "o cookie resolve para algum
    usuário ativo?". Entre elas, `GET /admin/agents/{loc}/agent`, que devolve
    api_key, anthropic_api_key e o prompt em texto puro de QUALQUER tenant.
    """
    p = current_principal(admin_session)
    return p is not None and p.is_operator


def current_admin(admin_session: Optional[str] = Cookie(None)) -> Optional[str]:
    """Username do operador logado (para exibir no painel e para auditoria)."""
    p = current_principal(admin_session)
    return p.username if (p is not None and p.is_operator) else None


def require_admin(admin_session: Optional[str] = Cookie(None)) -> bool:
    """
    Barreira de autenticação para routers inteiros — RECUSA em vez de devolver bool.

    `verify_admin` só informa; quem esquece de checar o resultado deixa o endpoint
    aberto. Foi o que aconteceu em `admin/ai_agent.py`: 25 rotas, nenhuma checando,
    e o GET do agente devolvia api_key/elevenlabs_api_key/prompt em texto puro para
    qualquer um com a URL. Usada como `dependencies=[Depends(require_admin)]` no
    APIRouter, a proteção passa a valer por construção — rota nova nasce fechada.
    """
    p = current_principal(admin_session)
    if p is None:
        raise HTTPException(status_code=401, detail="Não autenticado.")
    if not p.is_operator:
        # 403, não 401: ele ESTÁ autenticado, só não é da equipe. Devolver 401 aqui
        # mandaria o cliente para a tela de login num loop sem explicação.
        raise HTTPException(status_code=403, detail="Área restrita à equipe.")
    return True


@router_publico.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = None):
    return templates.TemplateResponse("login.html", {"request": request, "error": error})


@router_publico.post("/login")
# O login era de graça (`password == env`); com scrypt cada tentativa custa ~37ms
# e 16MB, e este é o único endpoint não autenticado do projeto sem limite. Sem
# freio, um scanner prende os dois núcleos do VPS e as mensagens dos tenants —
# que passam pelo MESMO processo — começam a expirar. Também é o único freio de
# força bruta que existe hoje contra a conta do operador.
@limiter.limit("10/minute")
async def do_login(request: Request, username: str = Form(...), password: str = Form(...)):
    from services.admin_auth import authenticate, make_session_value

    usuario = await asyncio.to_thread(authenticate, username, password)
    if usuario is None:
        # Mensagem única de propósito: dizer "usuário não existe" entregaria de
        # graça quais contas existem.
        logger.warning(f"[AUTH] Login recusado para '{(username or '')[:32]}'.")
        return RedirectResponse(
            url="/admin/login?error=Usuário ou senha incorretos", status_code=303
        )

    response = RedirectResponse(url="/admin/dashboard", status_code=303)
    response.set_cookie(
        key="admin_session",
        value=make_session_value(usuario.username, usuario.password_hash),
        httponly=True,
        samesite="lax",
        # Derivado do esquema REAL da request, não de DEBUG. Com `secure` fixo em
        # produção, abrir o painel por http:// (IP direto, certificado vencido)
        # daria login 303 bem-sucedido cujo cookie o browser DESCARTA — loop mudo
        # de volta para o login, com a senha certa e sem erro na tela.
        secure=(request.headers.get("x-forwarded-proto", request.url.scheme) == "https"),
        max_age=86400 * 30,
    )
    logger.info(f"[AUTH] Operador '{usuario.username}' autenticado.")
    return response


@router_publico.get("/logout")
async def logout():
    response = RedirectResponse(url="/admin/login", status_code=303)
    response.delete_cookie("admin_session")
    return response


@router.post("/senha")
@limiter.limit("5/minute")
async def trocar_senha(
    request: Request,
    senha_atual: str = Form(...),
    senha_nova: str = Form(...),
    operador: Optional[str] = Depends(current_admin),
):
    """
    Troca a senha do operador logado.

    Existe porque, sem ela, a senha só sairia por UPDATE manual no banco: o env
    deixou de ser a fonte da verdade de propósito, então trocar ADMIN_PASSWORD e
    redeployar não muda nada. Rotacionar credencial não pode depender de SQL.

    Pede a senha ATUAL mesmo já havendo sessão válida: um cookie roubado não pode
    virar troca de senha, que trancaria o dono para fora da própria conta.
    """
    from services.admin_auth import authenticate, make_session_value, set_password

    if not operador:
        return RedirectResponse(url="/admin/login", status_code=303)

    if len(senha_nova or "") < 8:
        return RedirectResponse(
            url="/admin/dashboard?err=A nova senha precisa de pelo menos 8 caracteres.",
            status_code=303,
        )

    if await asyncio.to_thread(authenticate, operador, senha_atual) is None:
        logger.warning(f"[AUTH] Troca de senha recusada para '{operador}': senha atual errada.")
        return RedirectResponse(url="/admin/dashboard?err=Senha atual incorreta.", status_code=303)

    await asyncio.to_thread(set_password, operador, senha_nova)

    # A troca invalida TODA sessão do usuário — inclusive esta. Reemitimos o
    # cookie para quem acabou de trocar continuar logado; as outras morrem.
    from services.admin_auth import _buscar_usuario_sync

    atualizado = await asyncio.to_thread(_buscar_usuario_sync, operador)
    response = RedirectResponse(
        url="/admin/dashboard?msg=Senha alterada. As outras sessões foram encerradas.",
        status_code=303,
    )
    if atualizado:
        response.set_cookie(
            key="admin_session",
            value=make_session_value(atualizado.username, atualizado.password_hash),
            httponly=True,
            samesite="lax",
            secure=(request.headers.get("x-forwarded-proto", request.url.scheme) == "https"),
            max_age=86400 * 30,
        )
    return response


@router.post("/operadores")
@limiter.limit("20/minute")
async def criar_operador(
    request: Request,
    novo_usuario: str = Form(...),
    nova_senha: str = Form(...),
    operador: Optional[str] = Depends(current_admin),
):
    """Cria outro operador do painel."""
    from services.admin_auth import criar_usuario

    if not operador:
        return RedirectResponse(url="/admin/login", status_code=303)

    ok, msg = await asyncio.to_thread(criar_usuario, novo_usuario, nova_senha)
    chave = "msg" if ok else "err"
    return RedirectResponse(url=f"/admin/dashboard?{chave}={msg}", status_code=303)


@router.post("/operadores/ativo")
@limiter.limit("20/minute")
async def alternar_operador(
    request: Request,
    alvo: str = Form(...),
    ativo: str = Form(...),
    operador: Optional[str] = Depends(current_admin),
):
    """
    Ativa/desativa operador.

    As travas contra ficar sem ninguém ativo moram em `definir_ativo`, não aqui —
    a rota é só uma das portas, e a regra precisa valer para qualquer caminho.
    """
    from services.admin_auth import definir_ativo

    if not operador:
        return RedirectResponse(url="/admin/login", status_code=303)

    ok, msg = await asyncio.to_thread(
        definir_ativo, alvo, str(ativo).lower() in ("1", "true", "on", "sim"), operador
    )
    chave = "msg" if ok else "err"
    return RedirectResponse(url=f"/admin/dashboard?{chave}={msg}", status_code=303)


@router.post("/operadores/senha")
@limiter.limit("10/minute")
async def redefinir_senha_de_operador(
    request: Request,
    alvo: str = Form(...),
    nova_senha: str = Form(...),
    operador: Optional[str] = Depends(current_admin),
):
    """
    Redefine a senha de OUTRO operador (quem esqueceu a própria).

    Não pede a senha atual porque quem redefine não a conhece — por isso é uma
    rota separada de `/admin/senha`, que exige confirmação por ser a própria.
    Como o token deriva do hash, o alvo é deslogado de todo lugar na hora.
    """
    from services.admin_auth import redefinir_senha_de_outro

    if not operador:
        return RedirectResponse(url="/admin/login", status_code=303)

    ok, msg = await asyncio.to_thread(redefinir_senha_de_outro, alvo, nova_senha, operador)
    chave = "msg" if ok else "err"
    return RedirectResponse(url=f"/admin/dashboard?{chave}={msg}", status_code=303)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(
    request: Request,
    msg: str = None,
    err: str = None,
    authenticated: bool = Depends(verify_admin),
    operador: Optional[str] = Depends(current_admin),
):
    if not authenticated:
        return RedirectResponse(url="/admin/login", status_code=303)

    from services.admin_auth import listar_usuarios
    try:
        operadores = await asyncio.to_thread(listar_usuarios)
    except Exception as e:  # a lista nunca pode impedir o painel de abrir
        logger.error(f"Falha ao listar operadores: {e}")
        operadores = []

    tenants = token_manager.get_all_tenants()
    # Ordenar por data de criação ou nome da empresa
    tenants.sort(key=lambda x: x.company_name)
    
    # Busca os Agentes de IA e System Settings
    from data.database import SessionLocal
    from data.models import AIAgent, SystemSettings, OnboardingSubmission
    from sqlalchemy import func as _func
    db = SessionLocal()
    agent_map = {}
    pending_onboarding_map = {}
    system_settings = None
    try:
        # Filtra agentes do canal WhatsApp para a linha principal da tabela.
        # Outros canais (Instagram, etc) sao gerenciados via modal multi-canal.
        agents = db.query(AIAgent).filter(AIAgent.channel == "whatsapp").all()
        for a in agents:
            agent_map[a.location_id] = a

        # Contagem de submissões de onboarding pendentes por tenant (badge no card)
        try:
            pend_rows = (
                db.query(
                    OnboardingSubmission.tenant_location_id,
                    _func.count(OnboardingSubmission.id),
                )
                .filter(OnboardingSubmission.status == "pending")
                .group_by(OnboardingSubmission.tenant_location_id)
                .all()
            )
            pending_onboarding_map = {loc: cnt for loc, cnt in pend_rows}
        except Exception as e_pend:
            logger.warning(f"Falha ao contar onboarding pendente: {e_pend}")

        settings = db.query(SystemSettings).first()
        if settings:
            system_settings = {
                "admin_openrouter_key": settings.admin_openrouter_key,
                "admin_openrouter_model": settings.admin_openrouter_model,
                "admin_groq_api_key": settings.admin_groq_api_key,
                "admin_waha_url": settings.admin_waha_url,
                "admin_waha_api_key": settings.admin_waha_api_key,
                "master_engine": getattr(settings, "master_engine", "openrouter") or "openrouter",
                "admin_anthropic_key": getattr(settings, "admin_anthropic_key", "") or "",
                "admin_anthropic_model": getattr(settings, "admin_anthropic_model", "") or "",
            }
        else:
            system_settings = {
                "admin_openrouter_key": "",
                "admin_openrouter_model": "openai/gpt-4o",
                "admin_groq_api_key": "",
                "admin_waha_url": "",
                "admin_waha_api_key": "",
                "master_engine": "openrouter",
                "admin_anthropic_key": "",
                "admin_anthropic_model": "",
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
            "is_token_expired": False if t.pit_token else (t.is_token_expired if getattr(t, "mode", "ghl") == "ghl" else False),
            "zapi_instance_id": t.zapi_instance_id,
            "zapi_token": t.zapi_token,
            "zapi_client_token": t.zapi_client_token,
            "client_id": t.client_id,
            # BOOLEANOS, não os tokens. O template joga estes campos dentro do
            # `onclick` de cada card, então o valor real ia em texto claro para o
            # HTML de todo tenant — e o JS nunca precisou dele: todo consumo é
            # `if (pitToken)`, e os modais de edição abrem com o campo VAZIO
            # (`value = ''`). Era credencial exposta em troca de nada.
            #
            # O PIT é o pior dos dois: não expira (ver CLAUDE.md). O do Telegram dá
            # controle total do bot. Ambos ficavam legíveis para qualquer extensão
            # de navegador, "salvar página como", captura de tela ou XSS na sessão
            # do operador.
            "pit_token": bool(t.pit_token),
            "telegram_bot_token": bool(t.telegram_bot_token),
            "telegram_bot_username": t.telegram_bot_username,
            "pending_onboarding": int(pending_onboarding_map.get(t.location_id, 0)),
            # Provedor real de WhatsApp (zapi | waha) — a UI não deve assumir Z-API.
            "whatsapp_provider": getattr(t, "whatsapp_provider", "zapi") or "zapi",
            "waha_session": getattr(t, "waha_session", None),
        }

        agent = agent_map.get(t.location_id)
        if agent:
            t_dict["ai_agent_data"] = {
                "name": agent.name,
                "prompt": agent.prompt,
                "model": agent.model,
                "api_key": agent.api_key,
                "agent_engine": getattr(agent, "agent_engine", "langchain") or "langchain",
                "anthropic_model": getattr(agent, "anthropic_model", "") or "",
                "anthropic_api_key": getattr(agent, "anthropic_api_key", "") or "",
                "tts_provider": agent.tts_provider or "elevenlabs",
                "elevenlabs_api_key": agent.elevenlabs_api_key,
                "elevenlabs_voice_id": agent.elevenlabs_voice_id,
                "elevenlabs_speed": float(agent.elevenlabs_speed) if agent.elevenlabs_speed is not None else 1.0,
                "elevenlabs_stability": float(agent.elevenlabs_stability) if agent.elevenlabs_stability is not None else 0.5,
                "elevenlabs_similarity": float(agent.elevenlabs_similarity) if agent.elevenlabs_similarity is not None else 0.75,
                "fishaudio_api_key": agent.fishaudio_api_key,
                "fishaudio_voice_id": agent.fishaudio_voice_id,
                "fishaudio_model": agent.fishaudio_model or "s1",
                "fishaudio_speed": float(agent.fishaudio_speed) if agent.fishaudio_speed is not None else 1.0,
                "fishaudio_temperature": float(agent.fishaudio_temperature) if agent.fishaudio_temperature is not None else 0.7,
                "fishaudio_top_p": float(agent.fishaudio_top_p) if agent.fishaudio_top_p is not None else 0.7,
                "fishaudio_normalize_loudness": bool(agent.fishaudio_normalize_loudness) if agent.fishaudio_normalize_loudness is not None else True,
                "groq_api_key": agent.groq_api_key,
                "is_active": agent.is_active,
                "debounce_seconds": float(agent.debounce_seconds) if agent.debounce_seconds is not None else 1.5,
                "qualification_enabled": bool(getattr(agent, 'qualification_enabled', False)),
                "qualification_pipeline_id": getattr(agent, 'qualification_pipeline_id', '') or '',
                "qualification_stage_id": getattr(agent, 'qualification_stage_id', '') or '',
                "qualification_fields": getattr(agent, 'qualification_fields', None) or [],
                "qualification_summary_prompt": getattr(agent, 'qualification_summary_prompt', '') or '',
                "form_data": getattr(agent, 'form_data', None),
            }
        else:
            t_dict["ai_agent_data"] = None

        # Só consulta a Z-API quando ela é o provedor ATIVO da instância: numa
        # instância que migrou para o WAHA a credencial antiga fica dormente, e
        # perguntar por ela só produziria um "Escanear QR" da Z-API num número
        # que não é mais atendido por ela (além de uma chamada HTTP por tenant
        # em cada carregamento do painel).
        t_dict["zapi_connection_status"] = "NOT_CONFIGURED"
        if active_whatsapp_provider(t) == ZAPI:
            status_data = await zapi_service.get_status(t.zapi_instance_id, t.zapi_token, t.zapi_client_token)
            if status_data:
                t_dict["zapi_connection_status"] = "CONNECTED" if status_data.get("connected") else "DISCONNECTED"
                
        tenants_list.append(t_dict)

    # O app do Marketplace é um só, do admin, para todos os clientes: se está
    # configurado no ambiente, a instância não precisa pedir client id/secret.
    ghl_client_id = (app_settings.ghl_client_id or "").strip()

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "tenants": tenants_list,
            "message": msg,
            "error_msg": err,
            "system_settings": system_settings,
            "ghl_app_configured": bool(ghl_client_id),
            "ghl_app_hint": _mask_app_id(ghl_client_id),
            "operadores": operadores,
            "operador_atual": operador,
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

    # Um provedor de WhatsApp por instância. Bloquear só no card não basta —
    # este form posta direto no endpoint.
    from urllib.parse import quote
    from services.channel_policy import ZAPI, conflict_message, whatsapp_conflict
    blocking = whatsapp_conflict(tenant_data, ZAPI)
    if blocking:
        msg = quote(conflict_message(blocking, ZAPI))
        return RedirectResponse(
            url=f"/admin/dashboard?err={msg}&instance={location_id}&tab=canais",
            status_code=303,
        )

    # Atualiza pelo TokenManager direto ao banco
    try:
        token_manager.update_zapi_credentials(
            location_id=tenant_data.location_id,
            instance_id=instance_id.strip(),
            token=token.strip(),
            client_token=client_token.strip()
        )
        return RedirectResponse(
            url=f"/admin/dashboard?msg=Credenciais do Z-API atualizadas!&instance={location_id}&tab=canais",
            status_code=303,
        )
    except Exception as e:
        logger.error(f"Erro ao salvar z-api: {e}")
        return RedirectResponse(url="/admin/dashboard?err=Erro interno salvar.", status_code=303)


@router.post("/system/save")
async def save_system_settings(
    admin_openrouter_key: str = Form(""),
    admin_openrouter_model: str = Form("openai/gpt-4o"),
    admin_groq_api_key: str = Form(""),
    admin_waha_url: str = Form(""),
    admin_waha_api_key: str = Form(""),
    master_engine: str = Form("openrouter"),
    admin_anthropic_key: str = Form(""),
    admin_anthropic_model: str = Form(""),
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
        settings.admin_groq_api_key = admin_groq_api_key.strip()
        settings.admin_waha_url = admin_waha_url.strip().rstrip("/") or None
        # Só sobrescreve a API key do WAHA se veio preenchida (permite editar a URL sem reenviar a key).
        if admin_waha_api_key.strip():
            settings.admin_waha_api_key = admin_waha_api_key.strip()

        # IA Mestre: motor + config Anthropic. Só sobrescreve a chave Anthropic se
        # veio preenchida (permite trocar o motor/modelo sem reenviar a chave).
        settings.master_engine = "anthropic" if master_engine.strip().lower() == "anthropic" else "openrouter"
        settings.admin_anthropic_model = admin_anthropic_model.strip() or None
        if admin_anthropic_key.strip():
            settings.admin_anthropic_key = admin_anthropic_key.strip()

        db.commit()
        # O servidor WAHA e resolvido com cache no envio — invalida para valer ja.
        try:
            from services.waha_service import invalidate_global_waha_config
            invalidate_global_waha_config()
        except Exception:
            pass
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

        def _zerado() -> dict:
            return {
                "calls": 0, "input_tokens": 0, "output_tokens": 0,
                "cache_read_tokens": 0, "cache_write_tokens": 0,
                "characters": 0, "buscas_web": 0,
                "cost_usd": 0.0, "sem_preco": 0,
            }

        def _somar(alvo: dict, log) -> None:
            alvo["calls"] += 1
            alvo["input_tokens"] += log.input_tokens or 0
            alvo["output_tokens"] += log.output_tokens or 0
            alvo["cache_read_tokens"] += getattr(log, "cache_read_tokens", 0) or 0
            alvo["cache_write_tokens"] += getattr(log, "cache_write_tokens", 0) or 0
            alvo["characters"] += log.characters or 0
            alvo["buscas_web"] += getattr(log, "buscas_web", 0) or 0
            # `cost_usd` NULL = não precificado (modelo fora da tabela de preços).
            # Somar como zero seria repetir o defeito que este painel tinha: um
            # total que parece de graça quando na verdade é desconhecido.
            if log.cost_usd is None:
                alvo["sem_preco"] += 1
            else:
                alvo["cost_usd"] += log.cost_usd

        by_service: dict = {}
        by_origem: dict = {}
        daily: dict = {}
        for log in logs:
            _somar(by_service.setdefault(log.service, _zerado()), log)
            origem = getattr(log, "origem", None) or "atendimento"
            _somar(by_origem.setdefault(origem, _zerado()), log)

            day_key = log.created_at.strftime("%Y-%m-%d") if log.created_at else "unknown"
            dia = daily.setdefault(day_key, {"calls": 0, "cost_usd": 0.0})
            dia["calls"] += 1
            dia["cost_usd"] += log.cost_usd or 0.0

        total_calls = sum(s["calls"] for s in by_service.values())
        total_cost = sum(s["cost_usd"] for s in by_service.values())
        sem_preco = sum(s["sem_preco"] for s in by_service.values())

        for bloco in list(by_service.values()) + list(by_origem.values()):
            bloco["cost_usd"] = round(bloco["cost_usd"], 6)

        return JSONResponse({
            "location_id": location_id,
            "period_days": days,
            "total_calls": total_calls,
            "total_cost_usd": round(total_cost, 6),
            # Quantas chamadas entraram no período sem preço tabelado. A tela
            # mostra este número junto do total — sem ele, "$0,12" pareceria a
            # conta inteira mesmo quando metade das chamadas não foi precificada.
            "chamadas_sem_preco": sem_preco,
            "by_service": by_service,
            "by_origem": by_origem,
            "daily": dict(sorted(daily.items())),
        })
    finally:
        db.close()


@router.post("/onboard-pit")
async def onboard_pit(
    company_name: str = Form(...),
    pit_token: str = Form(...),
    location_id: str = Form(...),
    zapi_instance_id: str = Form(""),
    zapi_token: str = Form(""),
    zapi_client_token: str = Form(""),
    authenticated: bool = Depends(verify_admin)
):
    """Cria um tenant usando Private Integration Token do GHL (sem OAuth)."""
    if not authenticated:
        return RedirectResponse(url="/admin/login", status_code=303)

    try:
        tenant = token_manager.register_pit_tenant(
            company_name=company_name.strip(),
            pit_token=pit_token.strip(),
            location_id=location_id.strip(),
        )
        if zapi_instance_id.strip():
            token_manager.update_zapi_credentials(
                location_id=tenant.location_id,
                instance_id=zapi_instance_id.strip(),
                token=zapi_token.strip(),
                client_token=zapi_client_token.strip(),
            )
        return RedirectResponse(
            url=f"/admin/dashboard?msg=Instância '{tenant.company_name}' criada com PIT!",
            status_code=303
        )
    except Exception as e:
        logger.error(f"Erro ao criar tenant PIT: {e}")
        return RedirectResponse(url=f"/admin/dashboard?err=Erro ao criar instância: {str(e)}", status_code=303)


@router.post("/tenant/{location_id}/pit")
async def update_tenant_pit(
    location_id: str,
    pit_token: str = Form(...),
    ghl_location_id: str = Form(""),
    authenticated: bool = Depends(verify_admin)
):
    """Atualiza/adiciona PIT a um tenant existente."""
    if not authenticated:
        return RedirectResponse(url="/admin/login", status_code=303)

    success = token_manager.update_pit_token(location_id, pit_token.strip())
    if not success:
        return RedirectResponse(url="/admin/dashboard?err=Tenant não encontrado.", status_code=303)

    if ghl_location_id.strip():
        from data.database import SessionLocal
        db = SessionLocal()
        try:
            tenant = token_manager.get_tenant(location_id, db=db)
            if tenant:
                tenant.ghl_location_id = ghl_location_id.strip()
                db.commit()
        finally:
            db.close()

    # Volta para a instância na aba CRM, para o operador ver o status atualizado.
    return RedirectResponse(
        url=f"/admin/dashboard?msg=Axen CRM conectado via Token PIT.&instance={location_id}&tab=crm",
        status_code=303,
    )


@router.post("/tenant/{location_id}/crm/desconectar")
async def desconectar_crm(location_id: str, _: bool = Depends(require_admin)):
    """
    Solta a instância do CRM: apaga a credencial e volta para `whatsapp_only`.

    Até agora só existia caminho de ida. O painel já sabia DESENHAR o estado
    "Nenhum CRM conectado — leads ficam no painel" (`dashboard.js`), e o backend
    inteiro já sabia OPERAR nele: `handle_inbound` pula o espelho, `ai_gate` não
    consulta o portão do CRM, `qualification_handler` grava o lead localmente,
    `agent_wizard.tem_crm` para de exigir funil. Faltava só o botão que leva até lá.

    O que apaga e por quê:

    · `pit_token`, `access_token`, `refresh_token`, `token_expires_at` — a
      credencial em si. Sem isto, "desconectado" seria só um rótulo: o token
      continuaria no banco e `get_valid_token` continuaria devolvendo ele.
    · `mode` volta a `whatsapp_only` — é a chave que o resto do código lê. Apagar
      a credencial sem mudar o modo deixaria o sistema tentando falar com um CRM
      que não tem mais como autenticar, falhando de fininho em cada mensagem.
    · `contact_mappings` e `message_mappings` — são apontadores para IDs DENTRO do
      CRM antigo. Guardá-los faria a próxima conexão espelhar mensagem em contato
      de outra conta. Apagar é seguro porque `resolve_contact_id` procura o
      contato pelo telefone antes de criar: reconectar o MESMO CRM reencontra
      todo mundo, e conectar outro cria do zero, que é o certo.

    O que NÃO apaga:

    · `qualified_leads`, `chat_histories`, os agentes e o prompt — isso é dado
      NOSSO, não do CRM. Desconectar o CRM não é apagar o trabalho da instância.
    · As credenciais de canal (WAHA/Z-API/Telegram). O WhatsApp continua no ar e
      a IA continua respondendo; só param de existir o espelho e o funil.
    """
    from data.database import SessionLocal
    from data.models import ContactMapping, MessageMapping, Tenant

    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter(Tenant.location_id == location_id).first()
        if not tenant:
            return JSONResponse({"success": False, "error": "Instância não encontrada."},
                                status_code=404)

        tinha = bool((tenant.pit_token or "").strip() or (tenant.access_token or "").strip())

        tenant.pit_token = None
        tenant.access_token = None
        tenant.refresh_token = None
        tenant.token_expires_at = None
        tenant.mode = "whatsapp_only"

        contatos = db.query(ContactMapping).filter(
            ContactMapping.location_id == location_id).delete(synchronize_session=False)
        mensagens = db.query(MessageMapping).filter(
            MessageMapping.location_id == location_id).delete(synchronize_session=False)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"[CRM] Falha ao desconectar {location_id}: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": f"Falha ao desconectar: {e}"},
                            status_code=500)
    finally:
        db.close()

    # Sem invalidação de cache aqui de propósito: `token_manager.get_tenant` lê do
    # banco a cada chamada (não há cache de tenant em memória), então o commit
    # acima já é a fonte da verdade para o próximo inbound.
    logger.info(
        f"[CRM] Desconectado de {location_id} (tinha credencial: {tinha}); "
        f"{contatos} contact_mappings e {mensagens} message_mappings removidos."
    )
    return JSONResponse({
        "success": True,
        "tinha_credencial": tinha,
        "contatos_removidos": contatos,
        "mensagens_removidas": mensagens,
    })


@router.post("/test-pit")
async def test_pit_connection(
    request: Request,
    authenticated: bool = Depends(verify_admin)
):
    """Testa se um PIT token é válido fazendo uma chamada à API do GHL."""
    if not authenticated:
        return JSONResponse({"success": False, "error": "Não autenticado."}, status_code=401)

    import httpx
    body = await request.json()
    pit_token = body.get("pit_token", "").strip()
    location_id = body.get("location_id", "").strip()

    if not pit_token:
        return JSONResponse({"success": False, "error": "Token PIT não informado."})

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            headers = {
                "Authorization": f"Bearer {pit_token}",
                "Content-Type": "application/json",
                "Version": "2021-07-28",
            }

            if location_id:
                resp = await client.get(
                    f"{app_settings.ghl_api_base}/locations/{location_id}",
                    headers=headers,
                )
            else:
                resp = await client.get(
                    f"{app_settings.ghl_api_base}/users/",
                    headers=headers,
                )

            if resp.status_code == 200:
                data = resp.json()
                loc = data.get("location", data)
                name = loc.get("name") or loc.get("companyName") or ""
                loc_id = loc.get("id") or location_id or ""
                return JSONResponse({
                    "success": True,
                    "location_name": name,
                    "location_id": loc_id,
                })
            elif resp.status_code == 401:
                return JSONResponse({"success": False, "error": "Token inválido ou sem permissão."})
            else:
                return JSONResponse({"success": False, "error": f"GHL retornou {resp.status_code}"})
    except Exception as e:
        logger.error(f"Erro ao testar PIT: {e}")
        return JSONResponse({"success": False, "error": f"Erro de conexão: {str(e)}"})


@router.post("/tenant/{location_id}/telegram")
async def save_telegram_config(
    request: Request,
    location_id: str,
    bot_token: str = Form(...),
    authenticated: bool = Depends(verify_admin),
):
    """Salva o bot_token do Telegram e registra o webhook automaticamente."""
    if not authenticated:
        return RedirectResponse(url="/admin/login", status_code=303)

    bot_token = bot_token.strip()
    bot_info = await telegram_service.get_me(bot_token)
    if not bot_info:
        return RedirectResponse(
            url="/admin/dashboard?err=Token do bot inválido. Confira o token recebido do @BotFather.",
            status_code=303,
        )

    from data.database import SessionLocal
    db = SessionLocal()
    try:
        tenant = token_manager.get_tenant(location_id, db=db)
        if not tenant:
            return RedirectResponse(url="/admin/dashboard?err=Tenant não encontrado.", status_code=303)

        tenant.telegram_bot_token = bot_token
        tenant.telegram_bot_username = bot_info.get("username")
        db.commit()
    finally:
        db.close()

    # A conta de canal do Telegram — sem ela, o bot novo nunca teria linha própria.
    from services.channel_accounts import sincronizar as _sincronizar_conta
    await _sincronizar_conta(location_id, "telegram")

    # Resolve a URL pública. Prioridade:
    # 1. PUBLIC_BASE_URL (configurado no .env)
    # 2. X-Forwarded-Host / X-Forwarded-Proto (proxy reverso)
    # 3. request.base_url (último recurso, normalmente HTTP em dev)
    public_base = (app_settings.public_base_url or "").strip().rstrip("/")
    if not public_base:
        forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
        forwarded_proto = request.headers.get("x-forwarded-proto", "https")
        if forwarded_host:
            public_base = f"{forwarded_proto}://{forwarded_host}"
        else:
            public_base = str(request.base_url).rstrip("/")

    # Telegram exige HTTPS. Força protocolo se vier HTTP de um proxy reverso.
    if public_base.startswith("http://") and "localhost" not in public_base and "127.0.0.1" not in public_base:
        public_base = "https://" + public_base[len("http://"):]

    webhook_url = f"{public_base}/webhook/telegram/{location_id}"
    logger.info(f"Tentando registrar webhook Telegram: {webhook_url}")
    ok = await telegram_service.set_webhook(bot_token, webhook_url)

    if not ok:
        return RedirectResponse(
            url=f"/admin/dashboard?err=Token salvo mas falhou ao registrar webhook em {webhook_url}. Confira se a URL é HTTPS público acessível.",
            status_code=303,
        )

    return RedirectResponse(
        url=f"/admin/dashboard?msg=Telegram conectado com sucesso! Bot: @{bot_info.get('username')}&instance={location_id}&tab=canais",
        status_code=303,
    )


@router.post("/tenant/{location_id}/telegram/resync-webhook")
async def resync_telegram_webhook(
    request: Request, location_id: str, authenticated: bool = Depends(verify_admin)
):
    """Re-registra o webhook do Telegram com a URL pública resolvida atual."""
    if not authenticated:
        return JSONResponse({"success": False, "error": "Não autenticado."}, status_code=401)

    tenant = token_manager.get_tenant(location_id)
    if not tenant or not tenant.telegram_bot_token:
        return JSONResponse({"success": False, "error": "Tenant sem bot configurado."})

    public_base = (app_settings.public_base_url or "").strip().rstrip("/")
    if not public_base:
        forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
        forwarded_proto = request.headers.get("x-forwarded-proto", "https")
        if forwarded_host:
            public_base = f"{forwarded_proto}://{forwarded_host}"
        else:
            public_base = str(request.base_url).rstrip("/")
    if public_base.startswith("http://") and "localhost" not in public_base and "127.0.0.1" not in public_base:
        public_base = "https://" + public_base[len("http://"):]

    webhook_url = f"{public_base}/webhook/telegram/{location_id}"
    ok = await telegram_service.set_webhook(tenant.telegram_bot_token, webhook_url)
    return JSONResponse({"success": ok, "webhook_url": webhook_url})


@router.post("/test-telegram")
async def test_telegram_token(request: Request, authenticated: bool = Depends(verify_admin)):
    """Valida um bot token do Telegram (botão Testar Conexão na UI)."""
    if not authenticated:
        return JSONResponse({"success": False, "error": "Não autenticado."}, status_code=401)

    body = await request.json()
    bot_token = body.get("bot_token", "").strip()
    if not bot_token:
        return JSONResponse({"success": False, "error": "Token não informado."})

    info = await telegram_service.get_me(bot_token)
    if info:
        return JSONResponse({
            "success": True,
            "bot_username": info.get("username"),
            "bot_name": info.get("first_name"),
        })
    return JSONResponse({"success": False, "error": "Token inválido."})


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


@router.post("/onboard-lite")
async def onboard_lite(
    request: Request,
    company_name: str = Form(...),
    authenticated: bool = Depends(verify_admin)
):
    """Cria um tenant 'lite' — só onboarding, gera link público pro cliente preencher."""
    if not authenticated:
        return RedirectResponse(url="/admin/login", status_code=303)

    name = company_name.strip()
    if not name:
        return RedirectResponse(url="/admin/dashboard?err=Informe o nome da empresa.", status_code=303)

    try:
        tenant = token_manager.create_lite_tenant(company_name=name)
        base_url = str(request.base_url).rstrip("/")
        form_url = f"{base_url}/form/{tenant.form_token}"
        from urllib.parse import quote
        return RedirectResponse(
            url=(
                f"/admin/dashboard?msg=Instância '{tenant.company_name}' criada. Compartilhe o link com o cliente."
                f"&onboarding_link={quote(form_url)}"
                f"&onboarding_company={quote(tenant.company_name)}"
            ),
            status_code=303
        )
    except Exception as e:
        logger.error(f"Erro ao criar tenant lite: {e}")
        return RedirectResponse(url=f"/admin/dashboard?err=Erro ao criar instância: {str(e)}", status_code=303)


# Aplicado no fim do módulo, depois de `require_admin` estar definido: fecha TODAS
# as rotas de `router` de uma vez. Vale também para as que ainda usam
# `Depends(verify_admin)` internamente — a barreira do router recusa antes.
router.dependencies.append(Depends(require_admin))
