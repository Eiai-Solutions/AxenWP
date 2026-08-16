"""
Rotas públicas do formulário de onboarding para clientes.

O cliente preenche informações da empresa e os dados são gravados na hora em
onboarding_submissions. A geração do prompt e a criação do agente são decisões
SEPARADAS, feitas pelo operador depois no dashboard — assim os dados do cliente
nunca se perdem mesmo que a IA Mestre esteja fora do ar.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from data.database import SessionLocal
from data.models import Tenant, OnboardingSubmission
from utils.validators import is_valid_form_token
from utils.limiter import limiter

router = APIRouter(prefix="/form", tags=["public_form"])
logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="web/templates")


@router.get("/{form_token}", response_class=HTMLResponse)
async def show_onboarding_form(request: Request, form_token: str):
    """Exibe o formulário público de onboarding."""
    if not is_valid_form_token(form_token):
        return HTMLResponse(
            content="<h1 style='color:#fff;font-family:sans-serif;text-align:center;margin-top:100px;'>Link invalido.</h1>",
            status_code=400
        )
    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter(Tenant.form_token == form_token).first()
        if not tenant:
            return HTMLResponse(
                content="<h1 style='color:#fff;font-family:sans-serif;text-align:center;margin-top:100px;'>Link invalido ou expirado.</h1>",
                status_code=404
            )

        return templates.TemplateResponse("onboarding_form.html", {
            "request": request,
            "company_name": tenant.company_name,
            "form_token": form_token,
        })
    finally:
        db.close()


@router.post("/{form_token}/submit")
@limiter.limit("5/minute")
async def submit_onboarding_form(
    request: Request,
    form_token: str,
    company_name: str = Form(""),
    industry: str = Form(""),
    company_description: str = Form(""),
    target_audience: str = Form(""),
    website: str = Form(""),
    instagram: str = Form(""),
    products_services: str = Form(""),
    differentials: str = Form(""),
    faq: str = Form(""),
    tone: str = Form(""),
    business_hours: str = Form(""),
    contact_info: str = Form(""),
    agent_goal: str = Form(""),
    extra_info: str = Form(""),
):
    """
    Grava as respostas do formulário IMEDIATAMENTE em onboarding_submissions.

    Não chama a IA Mestre nem cria agente — isso é decisão posterior do operador.
    Assim, mesmo sem OpenRouter configurado ou se a geração falhar, os dados do
    cliente ficam salvos e nunca se perdem.
    """
    if not is_valid_form_token(form_token):
        return JSONResponse({"success": False, "error": "Token inválido."}, status_code=400)

    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter(Tenant.form_token == form_token).first()
        if not tenant:
            return JSONResponse({"success": False, "error": "Link invalido."}, status_code=404)

        # Apenas os campos preenchidos pelo cliente. Campos definidos pelo
        # operador (agent_name, agent_type, tone_register, restrictions,
        # qualification_questions) entram só na hora de gerar o agente.
        form_answers = {
            "company_name": company_name,
            "industry": industry,
            "company_description": company_description,
            "target_audience": target_audience,
            "website": website,
            "instagram": instagram,
            "products_services": products_services,
            "differentials": differentials,
            "faq": faq,
            "tone": tone,
            "business_hours": business_hours,
            "contact_info": contact_info,
            "agent_goal": agent_goal,
            "extra_info": extra_info,
        }

        submission = OnboardingSubmission(
            tenant_location_id=tenant.location_id,
            form_data=form_answers,
            status="pending",
            created_at=datetime.now(timezone.utc),
        )
        db.add(submission)
        db.commit()
        logger.info(
            f"Onboarding salvo: submission #{submission.id} para tenant "
            f"{tenant.location_id} ({tenant.company_name})"
        )

        return JSONResponse({"success": True})

    except Exception as e:
        logger.error(f"Erro ao salvar formulário de onboarding: {e}", exc_info=True)
        db.rollback()
        return JSONResponse({"success": False, "error": "Erro interno. Tente novamente."})
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Entrevista com a IA Mestre — a porta principal; o formulário acima é o atalho
# --------------------------------------------------------------------------- #
@router.get("/{form_token}/entrevista", response_class=HTMLResponse)
async def pagina_entrevista(request: Request, form_token: str):
    """Tela da conversa. O agente sai daqui sem o cliente preencher formulário."""
    if not is_valid_form_token(form_token):
        return HTMLResponse(content="<h1>Link invalido.</h1>", status_code=400)

    from services.interview_session import resolver_tenant

    location_id = await resolver_tenant(form_token)
    if not location_id:
        return HTMLResponse(content="<h1>Link invalido ou expirado.</h1>", status_code=404)

    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter(Tenant.form_token == form_token).first()
        nome = tenant.company_name if tenant else ""
    finally:
        db.close()

    return templates.TemplateResponse("entrevista.html", {
        "request": request, "form_token": form_token, "company_name": nome,
    })


@router.post("/{form_token}/entrevista/mensagem")
# Teto por IP. Esta rota é ANÔNIMA e cada turno gasta a NOSSA chave Anthropic —
# sem freio, uma aba esquecida (ou um curioso) vira conta aberta. Os tetos de
# turno e de token por entrevista moram em `master_interview`; este é o de fora.
@limiter.limit("20/minute")
async def turno_entrevista(request: Request, form_token: str):
    if not is_valid_form_token(form_token):
        return JSONResponse({"success": False, "error": "Token inválido."}, status_code=400)

    from services.interview_session import (
        EntrevistaIndisponivel, SessaoInvalida, conversar, iniciar, resolver_tenant,
    )

    location_id = await resolver_tenant(form_token)
    if not location_id:
        return JSONResponse({"success": False, "error": "Link invalido."}, status_code=404)

    try:
        corpo = await request.json()
    except Exception:
        corpo = {}

    token = (corpo.get("token") or "").strip()
    mensagem = (corpo.get("mensagem") or "").strip()

    try:
        if not token:
            resultado = await iniciar(location_id)
        else:
            resultado = await conversar(token, mensagem or None, location_id_esperado=location_id)
    except EntrevistaIndisponivel as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=429)
    except SessaoInvalida as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=404)
    except Exception as e:
        logger.error(f"[ENTREVISTA] Falha no turno de {location_id}: {type(e).__name__}: {e}")
        return JSONResponse(
            {"success": False, "error": "Não consegui continuar agora. Tente de novo."},
            status_code=502,
        )

    # `iniciar` devolve o token novo; nos turnos seguintes ele volta como veio.
    return JSONResponse({"success": True, **resultado, "token": resultado.get("token") or token})
