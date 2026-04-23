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

        # Montar contexto com todas as informações do formulário
        agent_type_label = "OUTBOUND (Ativo — inicia contato com leads)" if agent_type == "outbound" else "INBOUND (Passivo — responde clientes que entram em contato)"
        company_context = f"""
TIPO DE ATENDIMENTO: {agent_type_label}

INFORMAÇÕES DA EMPRESA:
- Nome: {company_name}
- Segmento: {industry}
- Descrição: {company_description}
- Público-alvo: {target_audience or 'Não especificado'}
- Website: {website or 'Não informado'}
- Instagram: {instagram or 'Não informado'}

PRODUTOS/SERVIÇOS:
{products_services}

DIFERENCIAIS:
{differentials or 'Não informado'}

PERGUNTAS FREQUENTES (FAQ):
{faq or 'Nenhuma informada'}

CONFIGURAÇÃO DO AGENTE:
- Nome do agente: {agent_name}
- Tom de voz: {tone or 'Não especificado'}
- Horário de funcionamento: {business_hours or 'Não informado'}
- Contatos para transferência: {contact_info or 'Não informado'}

OBJETIVO PRINCIPAL:
{agent_goal}

RESTRIÇÕES (o que NÃO fazer):
{restrictions or 'Nenhuma especificada'}

PERGUNTAS QUALIFICATÓRIAS (para qualificar o lead antes de transferir):
{qualification_questions or 'Nenhuma definida'}

INFORMAÇÕES ADICIONAIS:
{extra_info or 'Nenhuma'}
""".strip()

        # Gerar prompt com IA Mestre
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "max_tokens": 6000,
                    "messages": [
                        {"role": "system", "content": (
                            "Você é um especialista sênior em Prompt Engineering para agentes de IA de WhatsApp.\n\n"
                            "Sua tarefa: receber informações sobre uma empresa e criar um PROMPT DE SISTEMA completo, "
                            "detalhado e profissional para o agente de IA que vai atender os clientes dessa empresa via WhatsApp.\n\n"
                            "═══════════════════════════════════════════════\n"
                            "ADAPTE RADICALMENTE O PROMPT AO TIPO DE ATENDIMENTO\n"
                            "═══════════════════════════════════════════════\n\n"
                            "▸ SE INBOUND (passivo): o agente RECEBE mensagens de clientes já interessados.\n"
                            "  REGRAS DE COMPORTAMENTO:\n"
                            "  - Cumprimento acolhedor + pergunta aberta do tipo 'como posso ajudar?'\n"
                            "  - Tom acolhedor e prestativo\n"
                            "  - Tirar dúvidas, informar sobre produtos/preços, qualificar intenção\n"
                            "  - Direcionar para agendamento/compra quando demonstrar interesse\n\n"
                            "▸ SE OUTBOUND (ativo/prospecção): o agente INICIA conversa com leads frios.\n"
                            "  REGRAS CRÍTICAS — O AGENTE NÃO PODE:\n"
                            "  - ❌ Perguntar 'como posso te ajudar?' (cliente não pediu ajuda!)\n"
                            "  - ❌ Cumprimentar e ficar esperando o cliente dizer o que quer\n"
                            "  - ❌ Agir como atendimento reativo\n\n"
                            "  REGRAS CRÍTICAS — O AGENTE DEVE:\n"
                            "  - ✅ Abrir com uma PERGUNTA DIRETA SOBRE O PRODUTO/DOR que gere resposta\n"
                            "    Exemplos (adaptar ao segmento):\n"
                            "    • Seguros: 'Oi, [Nome]! Você e sua família já têm seguro de vida?'\n"
                            "    • Energia solar: 'Oi, [Nome]! Sua conta de luz tá vindo acima de R$ 300?'\n"
                            "    • Academia: 'Oi, [Nome]! Tá a quanto tempo sem treinar?'\n"
                            "    • Consultoria: 'Oi, [Nome]! Seu negócio tá batendo meta esse mês?'\n"
                            "  - ✅ Criar MENSAGEM DE ABERTURA FIXA (primeira msg) com a pergunta direta\n"
                            "  - ✅ Após a resposta, CONDUZIR a conversa com perguntas de qualificação\n"
                            "  - ✅ Apresentar valor/benefício ANTES de pedir qualquer coisa\n"
                            "  - ✅ Se o lead disser 'não tenho interesse', agradecer e encerrar (sem insistir)\n"
                            "  - ✅ Tom consultivo + provocativo (gerar reflexão sobre o problema)\n\n"
                            "  NO PROMPT OUTBOUND INCLUA OBRIGATORIAMENTE UMA SEÇÃO:\n"
                            "  ## MENSAGEM DE ABERTURA (SEMPRE USAR NA PRIMEIRA INTERAÇÃO)\n"
                            "  [Texto exato da primeira mensagem que o agente DEVE enviar,\n"
                            "   com a pergunta direta sobre o produto/dor do segmento da empresa]\n\n"
                            "  REGRA CRÍTICA: Se o lead mandar apenas saudação tipo 'Oi', 'Bom dia', 'Olá',\n"
                            "  ou qualquer cumprimento vazio SEM fazer pergunta/pedido específico,\n"
                            "  o agente DEVE responder com a MENSAGEM DE ABERTURA (pergunta direta sobre o produto).\n"
                            "  NUNCA responder a saudação com 'como posso ajudar?' no modo outbound.\n\n"
                            "  E outra seção:\n"
                            "  ## FLUXO DE QUALIFICAÇÃO (após resposta à abertura)\n"
                            "  [Sequência de perguntas para qualificar interesse, orçamento, urgência]\n\n"
                            "═══════════════════════════════════════════════\n\n"
                            "O prompt deve também:\n"
                            "1. Definir claramente a identidade do agente (nome, personalidade, tom)\n"
                            "2. Descrever o que a empresa faz e seus serviços/produtos com detalhes\n"
                            "3. Incluir regras de comportamento e restrições\n"
                            "4. Ter seções organizadas para FAQ, quando possível\n"
                            "5. Definir quando e como transferir para um humano\n"
                            "6. Ser otimizado para conversas de WhatsApp (respostas concisas mas completas)\n"
                            "7. Incluir instruções para lidar com objeções e perguntas fora do escopo\n"
                            "8. Usar formatação clara com seções e marcadores\n\n"
                            "IMPORTANTE:\n"
                            "- Retorne APENAS o prompt, sem explicações ou comentários\n"
                            "- O prompt deve estar em português brasileiro\n"
                            "- Use as informações fornecidas, NÃO invente dados (preços, horários, etc.) que não foram informados\n"
                            "- Se alguma informação não foi fornecida, instrua o agente a direcionar o cliente para falar com um humano sobre esse assunto"
                        )},
                        {"role": "user", "content": (
                            f"Com base nas informações abaixo, crie o prompt de sistema para o agente de IA:\n\n"
                            f"{company_context}"
                        )}
                    ]
                }
            )

            if resp.status_code != 200:
                logger.error(f"Erro OpenRouter ao gerar prompt: {resp.status_code} — {resp.text}")
                return JSONResponse({
                    "success": False,
                    "error": "Erro ao gerar prompt. Tente novamente."
                })

            generated_prompt = resp.json()["choices"][0]["message"]["content"]

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
