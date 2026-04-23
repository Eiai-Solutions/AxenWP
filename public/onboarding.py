"""
Rotas públicas do formulário de onboarding para clientes.
O cliente preenche informações da empresa e a IA Mestre gera o prompt do agente.
"""

import logging
import uuid
from typing import Optional

import httpx
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from data.database import SessionLocal
from data.models import Tenant, AIAgent, SystemSettings
from auth.token_manager import token_manager
from utils.master_prompt import build_messages

router = APIRouter(prefix="/form", tags=["public_form"])
logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="web/templates")


def _openrouter_headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://axenwp.com",
        "X-Title": "AxenWP Prompt Generator",
    }


@router.get("/{form_token}", response_class=HTMLResponse)
async def show_onboarding_form(request: Request, form_token: str):
    """Exibe o formulário público de onboarding."""
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
async def submit_onboarding_form(
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
    agent_name: str = Form(""),
    tone: str = Form(""),
    business_hours: str = Form(""),
    contact_info: str = Form(""),
    agent_goal: str = Form(""),
    restrictions: str = Form(""),
    extra_info: str = Form(""),
    qualification_questions: str = Form(""),
    agent_type: str = Form("inbound"),
):
    """Recebe os dados do formulário e gera o prompt via IA Mestre."""
    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter(Tenant.form_token == form_token).first()
        if not tenant:
            return JSONResponse({"success": False, "error": "Link invalido."}, status_code=404)

        # Buscar configs da IA Mestre (admin)
        settings = db.query(SystemSettings).first()
        if not settings or not settings.admin_openrouter_key:
            return JSONResponse({
                "success": False,
                "error": "Sistema nao configurado. Contacte o administrador."
            })

        model = settings.admin_openrouter_model or "openai/gpt-4o"
        headers = _openrouter_headers(settings.admin_openrouter_key)

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
            "agent_name": agent_name,
            "tone": tone,
            "business_hours": business_hours,
            "contact_info": contact_info,
            "agent_goal": agent_goal,
            "restrictions": restrictions,
            "extra_info": extra_info,
            "qualification_questions": qualification_questions,
            "agent_type": agent_type,
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "max_tokens": 6000,
                    "messages": build_messages(form_answers),
                }
            )

            if resp.status_code != 200:
                logger.error(f"Erro OpenRouter ao gerar prompt: {resp.status_code} — {resp.text}")
                return JSONResponse({
                    "success": False,
                    "error": "Erro ao gerar prompt. Tente novamente."
                })

            generated_prompt = resp.json()["choices"][0]["message"]["content"]

        agent = db.query(AIAgent).filter(AIAgent.location_id == tenant.location_id).first()
        if not agent:
            agent = AIAgent(
                location_id=tenant.location_id,
                name=agent_name or "Agente Inteligente",
                prompt=generated_prompt,
                form_data=form_answers,
            )
            db.add(agent)
        else:
            agent.prompt = generated_prompt
            agent.form_data = form_answers
            if agent_name:
                agent.name = agent_name

        db.commit()
        logger.info(f"Prompt gerado via formulário para tenant {tenant.location_id} ({tenant.company_name})")

        return JSONResponse({"success": True})

    except Exception as e:
        logger.error(f"Erro ao processar formulário de onboarding: {e}", exc_info=True)
        db.rollback()
        return JSONResponse({"success": False, "error": "Erro interno. Tente novamente."})
    finally:
        db.close()
