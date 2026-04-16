import logging
import re
from typing import Optional
from fastapi import APIRouter, Depends, Form, HTTPException, Request
import httpx
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import json

from data.database import get_db, SessionLocal
from data.models import Tenant, AIAgent, SystemSettings, ChatHistory, QualifiedLead
from auth.token_manager import token_manager
from services.ghl_service import ghl_service
from datetime import datetime, timezone
from pydantic import BaseModel

router = APIRouter(prefix="/admin/agents", tags=["admin_agents"])
logger = logging.getLogger(__name__)

# Reutilizando o mesmo diretório de templates
templates = Jinja2Templates(directory="web/templates")

@router.post("/{location_id}/save")
async def save_agent_settings(
    location_id: str,
    name: str = Form(...),
    prompt: str = Form(...),
    model: str = Form("openai/gpt-4o"),
    api_key: Optional[str] = Form(None),
    elevenlabs_api_key: Optional[str] = Form(None),
    elevenlabs_voice_id: Optional[str] = Form(None),
    elevenlabs_speed: float = Form(1.0),
    elevenlabs_stability: float = Form(0.5),
    elevenlabs_similarity: float = Form(0.75),
    groq_api_key: Optional[str] = Form(None),
    is_active: bool = Form(False),
    debounce_seconds: float = Form(1.5),
    qualification_enabled: bool = Form(False),
    qualification_pipeline_id: Optional[str] = Form(None),
    qualification_stage_id: Optional[str] = Form(None),
    qualification_fields: Optional[str] = Form(None),
    qualification_summary_prompt: Optional[str] = Form(None),
    channel: str = Form("whatsapp"),
):
    """
    Cria ou atualiza as configurações do Agente de IA para um Tenant + canal específico.
    """
    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter(Tenant.location_id == location_id).first()
        if not tenant:
            logger.error(f"Tenant {location_id} não encontrado ao tentar salvar Agente IA.")
            return RedirectResponse(url="/admin/dashboard?err=Tenant+não+encontrado", status_code=303)

        # Busca agente existente ou cria novo (escopo: location_id + channel)
        agent = db.query(AIAgent).filter(
            AIAgent.location_id == location_id,
            AIAgent.channel == channel,
        ).first()

        if not agent:
            agent = AIAgent(location_id=location_id, channel=channel)
            db.add(agent)

        agent.name = name
        agent.prompt = prompt
        agent.model = model
        agent.api_key = api_key
        agent.elevenlabs_api_key = elevenlabs_api_key
        agent.elevenlabs_voice_id = elevenlabs_voice_id
        agent.elevenlabs_speed = max(0.25, min(float(elevenlabs_speed), 4.0))
        agent.elevenlabs_stability = max(0.0, min(float(elevenlabs_stability), 1.0))
        agent.elevenlabs_similarity = max(0.0, min(float(elevenlabs_similarity), 1.0))
        agent.groq_api_key = groq_api_key
        agent.is_active = is_active
        agent.debounce_seconds = max(0.5, min(float(debounce_seconds), 30.0))

        # Qualificação de leads
        agent.qualification_enabled = qualification_enabled
        agent.qualification_pipeline_id = qualification_pipeline_id or None
        agent.qualification_stage_id = qualification_stage_id or None
        agent.qualification_summary_prompt = qualification_summary_prompt or None

        # Parse dos campos de qualificação (JSON string do frontend)
        if qualification_fields:
            try:
                parsed_fields = json.loads(qualification_fields)
                agent.qualification_fields = parsed_fields if isinstance(parsed_fields, list) else None
            except (json.JSONDecodeError, ValueError):
                agent.qualification_fields = None
        else:
            agent.qualification_fields = None

        agent.updated_at = datetime.now(timezone.utc)

        db.commit()
        logger.info(f"Configurações do Agente IA atualizadas para o Tenant {location_id}.")

        return RedirectResponse(url="/admin/dashboard?msg=Agente+IA+atualizado+com+sucesso", status_code=303)

    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao salvar Agente IA para o tenant {location_id}: {e}", exc_info=True)
        return RedirectResponse(url="/admin/dashboard?err=Erro+ao+salvar+Agente+IA", status_code=303)
    finally:
        db.close()

@router.get("/{location_id}/list")
async def list_agents(location_id: str):
    """Lista todos os agentes (canais) configurados para um tenant."""
    db = SessionLocal()
    try:
        agents = db.query(AIAgent).filter(AIAgent.location_id == location_id).all()
        return {
            "success": True,
            "agents": [
                {
                    "id": a.id,
                    "channel": a.channel,
                    "name": a.name,
                    "is_active": a.is_active,
                    "model": a.model,
                    "qualification_enabled": bool(a.qualification_enabled),
                }
                for a in agents
            ],
        }
    except Exception as e:
        logger.error(f"Erro ao listar agentes: {e}")
        return {"success": False, "error": str(e)}
    finally:
        db.close()


@router.get("/{location_id}/agent")
async def get_agent_by_channel(location_id: str, channel: str = "whatsapp"):
    """Retorna a configuracao completa de um agente especifico por canal."""
    db = SessionLocal()
    try:
        agent = db.query(AIAgent).filter(
            AIAgent.location_id == location_id,
            AIAgent.channel == channel,
        ).first()
        if not agent:
            return {"success": True, "agent": None}
        return {
            "success": True,
            "agent": {
                "id": agent.id,
                "channel": agent.channel,
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
                "qualification_enabled": bool(agent.qualification_enabled),
                "qualification_pipeline_id": agent.qualification_pipeline_id or "",
                "qualification_stage_id": agent.qualification_stage_id or "",
                "qualification_fields": agent.qualification_fields or [],
                "qualification_summary_prompt": agent.qualification_summary_prompt or "",
            },
        }
    except Exception as e:
        logger.error(f"Erro ao buscar agente: {e}")
        return {"success": False, "error": str(e)}
    finally:
        db.close()


@router.delete("/{location_id}/agent")
async def delete_agent_by_channel(location_id: str, channel: str):
    """Remove um agente especifico por canal."""
    if channel == "whatsapp":
        return {"success": False, "error": "Nao e permitido deletar o agente do canal principal (whatsapp)."}
    db = SessionLocal()
    try:
        deleted = db.query(AIAgent).filter(
            AIAgent.location_id == location_id,
            AIAgent.channel == channel,
        ).delete()
        db.commit()
        logger.info(f"Agente {channel} removido para tenant {location_id}. Registros: {deleted}")
        return {"success": True, "deleted": deleted}
    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao remover agente: {e}")
        return {"success": False, "error": str(e)}
    finally:
        db.close()


@router.get("/{location_id}/ghl/pipelines")
async def get_ghl_pipelines(location_id: str):
    """Busca pipelines (funis de oportunidades) do GHL para o tenant."""
    try:
        # Verificar se tenant existe e tem token
        tenant = token_manager.get_tenant(location_id)
        if not tenant:
            return {"success": False, "error": "Tenant não encontrado."}

        token = await token_manager.get_valid_token(location_id)
        if not token:
            return {"success": False, "error": "Sem token GHL válido. Conecte o CRM nas configurações da instância."}

        result = await ghl_service.get_pipelines(location_id)
        if result.get("error"):
            return {"success": False, "error": result.get("message", "Erro desconhecido")}
        return {"success": True, "pipelines": result.get("pipelines", [])}
    except Exception as e:
        logger.error(f"Erro ao buscar pipelines GHL: {e}")
        return {"success": False, "error": str(e)}


@router.get("/{location_id}/ghl/custom-fields")
async def get_ghl_custom_fields(location_id: str, model: str = "all"):
    """Busca custom fields do GHL por modelo (contact ou opportunity)."""
    try:
        tenant = token_manager.get_tenant(location_id)
        if not tenant:
            return {"success": False, "error": "Tenant não encontrado."}

        token = await token_manager.get_valid_token(location_id)
        if not token:
            return {"success": False, "error": "Sem token GHL válido. Conecte o CRM nas configurações da instância."}

        result = await ghl_service.get_custom_fields(location_id, model=model)
        if result.get("error"):
            return {"success": False, "error": result.get("message", "Erro desconhecido")}
        return {"success": True, "fields": result.get("fields", [])}
    except Exception as e:
        logger.error(f"Erro ao buscar custom fields GHL: {e}")
        return {"success": False, "error": str(e)}


@router.get("/{location_id}/conversations")
async def get_conversations(location_id: str, offset: int = 0, limit: int = 20):
    """Retorna lista de conversas agrupadas por contato, paginadas."""
    from sqlalchemy import func, desc
    db = SessionLocal()
    try:
        # session_id = "{location_id}_{phone}"
        prefix = f"{location_id}_"
        contacts_q = (
            db.query(
                ChatHistory.session_id,
                func.count(ChatHistory.id).label("msg_count"),
                func.min(ChatHistory.created_at).label("first_msg"),
                func.max(ChatHistory.created_at).label("last_msg"),
            )
            .filter(ChatHistory.session_id.like(f"{prefix}%"))
            .group_by(ChatHistory.session_id)
            .order_by(desc("last_msg"))
            .offset(offset)
            .limit(limit)
        )

        results = contacts_q.all()
        total = (
            db.query(func.count(func.distinct(ChatHistory.session_id)))
            .filter(ChatHistory.session_id.like(f"{prefix}%"))
            .scalar()
        ) or 0

        # Verificar quais estão qualificados
        phones = [r.session_id[len(prefix):] for r in results]
        qualified_map = {}
        if phones:
            qualified = db.query(QualifiedLead).filter(
                QualifiedLead.location_id == location_id,
                QualifiedLead.phone.in_(phones),
            ).all()
            qualified_map = {q.phone: {
                "qualified_data": q.qualified_data,
                "summary": q.summary,
                "opportunity_id": q.ghl_opportunity_id,
                "qualified_at": q.created_at.isoformat() if q.created_at else None,
            } for q in qualified}

        contacts = []
        for r in results:
            phone = r.session_id[len(prefix):]
            contacts.append({
                "phone": phone,
                "session_id": r.session_id,
                "msg_count": r.msg_count,
                "first_msg": r.first_msg.isoformat() if r.first_msg else None,
                "last_msg": r.last_msg.isoformat() if r.last_msg else None,
                "qualified": qualified_map.get(phone),
            })

        # Buscar campos de qualificação do agente
        agent = db.query(AIAgent).filter(AIAgent.location_id == location_id).first()
        qual_fields = agent.qualification_fields if agent and agent.qualification_fields else []

        return {
            "success": True,
            "contacts": contacts,
            "total": total,
            "offset": offset,
            "limit": limit,
            "qualification_fields": qual_fields,
        }
    except Exception as e:
        logger.error(f"Erro ao buscar conversas: {e}")
        return {"success": False, "error": str(e)}
    finally:
        db.close()


@router.get("/{location_id}/conversations/{phone}/messages")
async def get_conversation_messages(location_id: str, phone: str, offset: int = 0, limit: int = 50):
    """Retorna mensagens de uma conversa específica, paginadas."""
    from sqlalchemy import func
    db = SessionLocal()
    try:
        session_id = f"{location_id}_{phone}"
        messages = (
            db.query(ChatHistory)
            .filter(ChatHistory.session_id == session_id)
            .order_by(ChatHistory.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        total = db.query(func.count(ChatHistory.id)).filter(ChatHistory.session_id == session_id).scalar() or 0

        return {
            "success": True,
            "messages": [{
                "type": m.message_type,
                "content": m.content,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            } for m in reversed(messages)],  # chronological order
            "total": total,
        }
    except Exception as e:
        logger.error(f"Erro ao buscar mensagens: {e}")
        return {"success": False, "error": str(e)}
    finally:
        db.close()


@router.get("/{location_id}/conversations/{phone}/progress")
async def get_qualification_progress(location_id: str, phone: str):
    """
    Retorna o progresso de qualificação de um lead.
    - Se qualificado: retorna os dados confirmados do QualifiedLead
    - Se em andamento: retorna o cache de progresso extraído pelo AI
    """
    from services.ai_service import _qual_progress_cache
    db = SessionLocal()
    try:
        agent = db.query(AIAgent).filter(AIAgent.location_id == location_id).first()
        qual_fields = agent.qualification_fields if agent and agent.qualification_fields else []
        if not qual_fields:
            return {"success": True, "progress": {}, "qualified": False}

        # Se já qualificado, retornar os dados confirmados
        qualified = db.query(QualifiedLead).filter(
            QualifiedLead.location_id == location_id,
            QualifiedLead.phone == phone,
        ).first()
        if qualified:
            return {
                "success": True,
                "progress": qualified.qualified_data or {},
                "qualified": True,
            }

        # Retornar progresso parcial do cache (extraído pelo AI em tempo real)
        session_id = f"{location_id}_{phone}"
        progress = _qual_progress_cache.get(session_id, {})
        return {"success": True, "progress": progress, "qualified": False}
    except Exception as e:
        logger.error(f"Erro ao buscar progresso de qualificação: {e}")
        return {"success": False, "error": str(e)}
    finally:
        db.close()


@router.delete("/{location_id}/conversations/{phone}/qualification")
async def reset_qualification(location_id: str, phone: str, clear_history: bool = False):
    """
    Remove a qualificação de um lead e limpa o cache de progresso.
    Se clear_history=true, apaga também o histórico de conversa (necessário para
    evitar que a IA re-qualifique imediatamente ao ler as mensagens anteriores).
    """
    from services.ai_service import _qual_progress_cache
    db = SessionLocal()
    try:
        deleted_qual = db.query(QualifiedLead).filter(
            QualifiedLead.location_id == location_id,
            QualifiedLead.phone == phone,
        ).delete()

        deleted_history = 0
        if clear_history:
            session_id = f"{location_id}_{phone}"
            deleted_history = db.query(ChatHistory).filter(
                ChatHistory.session_id == session_id,
            ).delete()

        db.commit()

        session_id = f"{location_id}_{phone}"
        _qual_progress_cache.pop(session_id, None)

        logger.info(
            f"Reset para {phone} @ {location_id}: qual={deleted_qual}, history={deleted_history}"
        )
        return {"success": True, "deleted_qual": deleted_qual, "deleted_history": deleted_history}
    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao resetar qualificação: {e}")
        return {"success": False, "error": str(e)}
    finally:
        db.close()


@router.get("/elevenlabs/voices")
async def get_elevenlabs_voices(api_key: str):
    """
    Busca a lista de vozes disponíveis na conta da ElevenLabs usando a API Key fornecida.
    """
    if not api_key:
        raise HTTPException(status_code=400, detail="API Key da ElevenLabs é obrigatória.")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                "https://api.elevenlabs.io/v1/voices",
                headers={"xi-api-key": api_key}
            )

            if response.status_code != 200:
                raise HTTPException(status_code=400, detail="Não foi possível verificar a API Key na ElevenLabs")

            data = response.json()
            voices = [{"voice_id": v["voice_id"], "name": v["name"]} for v in data.get("voices", [])]
            return {"success": True, "voices": voices}

    except Exception as e:
        logger.error(f"Erro ao buscar vozes na ElevenLabs: {e}")
        raise HTTPException(status_code=500, detail="Erro interno ao consultar serviço de voz.")


# ── Helpers para chamadas OpenRouter ──

def _openrouter_headers(api_key: str, title: str = "AxenWP") -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://axenwp.com",
        "X-Title": title,
    }

def _extract_tag(text: str, tag: str) -> str:
    """Extrai conteúdo entre tags XML. Usa greedy para capturar até o último fechamento."""
    m = re.search(rf"<{tag}>(.*)</{tag}>", text, re.DOTALL)
    return m.group(1).strip() if m else ""


def _apply_diffs(original: str, diffs_text: str) -> str:
    """
    Aplica diffs no formato <<<FIND>>>...<<<REPLACE>>>...<<<END>>> ao prompt original.
    O LLM nunca precisa gerar o prompt completo — só os trechos que mudam.
    O Python faz o find/replace preservando 100% do conteúdo inalterado.
    """
    result = original
    # Parse dos blocos de diff
    blocks = re.findall(
        r'<<<FIND>>>(.*?)<<<REPLACE>>>(.*?)<<<END>>>',
        diffs_text,
        re.DOTALL,
    )

    applied = 0
    for find_text, replace_text in blocks:
        find_text = find_text.strip()
        replace_text = replace_text.strip()

        if not find_text:
            continue

        if find_text in result:
            result = result.replace(find_text, replace_text, 1)
            applied += 1
        else:
            # Fallback: tentar match mais flexível (ignorando espaços extras)
            find_normalized = re.sub(r'\s+', r'\\s+', re.escape(find_text))
            m = re.search(find_normalized, result)
            if m:
                result = result[:m.start()] + replace_text + result[m.end():]
                applied += 1
            else:
                logger.warning(f"Diff não encontrado no prompt: '{find_text[:80]}...'")

    # Se há blocos <<<APPEND>>> para adicionar conteúdo novo ao final
    appends = re.findall(r'<<<APPEND>>>(.*?)<<<END>>>', diffs_text, re.DOTALL)
    for append_text in appends:
        append_text = append_text.strip()
        if append_text:
            result = result.rstrip() + "\n\n" + append_text
            applied += 1

    logger.info(f"Diffs aplicados: {applied}/{len(blocks) + len(appends)}")
    return result


_DIFF_SYSTEM_PROMPT = (
    "Você é um editor de prompts. Sua tarefa é gerar PATCHES (diffs) para modificar um prompt.\n\n"
    "FORMATO OBRIGATÓRIO — use exatamente este formato para cada mudança:\n\n"
    "<<<FIND>>>\n"
    "[trecho EXATO do prompt original que deve ser alterado — copie literalmente]\n"
    "<<<REPLACE>>>\n"
    "[novo conteúdo que substitui o trecho acima]\n"
    "<<<END>>>\n\n"
    "Para ADICIONAR conteúdo novo ao final do prompt (seções novas):\n"
    "<<<APPEND>>>\n"
    "[conteúdo a adicionar no final]\n"
    "<<<END>>>\n\n"
    "Para REMOVER um trecho, use <<<REPLACE>>> vazio:\n"
    "<<<FIND>>>\n[trecho a remover]\n<<<REPLACE>>>\n<<<END>>>\n\n"
    "REGRAS:\n"
    "- O texto em <<<FIND>>> DEVE ser uma cópia exata do prompt original (mesma capitalização, pontuação, quebras de linha)\n"
    "- Inclua contexto suficiente no FIND para ser único (pelo menos 2-3 linhas)\n"
    "- NUNCA gere o prompt completo — apenas os diffs\n"
    "- Cada bloco é um patch independente\n"
    "- Gere apenas os patches, sem explicações antes ou depois"
)


class AnalyzeRequest(BaseModel):
    prompt_text: str

@router.post("/analyze-prompt")
async def analyze_ai_prompt(payload: AnalyzeRequest):
    """
    Analisa e melhora o prompt do agente em 3 etapas:
      1. Simula conversa Lead vs Agente
      2. Gera análise + lista de mudanças específicas (NÃO gera prompt completo)
      3. Chamada separada que aplica as mudanças ao prompt original (sem abreviar)
    """
    db = SessionLocal()
    try:
        settings = db.query(SystemSettings).first()
        if not settings or not settings.admin_openrouter_key:
            return {"success": False, "error": "Chave API do Administrador não configurada. Configure no menu superior (Admin Settings)."}

        model = settings.admin_openrouter_model or "openai/gpt-4o"
        headers = _openrouter_headers(settings.admin_openrouter_key, "AxenWP Prompt Analyzer")
        _FALLBACK_MODEL = "openai/gpt-4o"

        async with httpx.AsyncClient(timeout=90.0) as client:

            # Helper: chama OpenRouter com fallback automático se 404
            async def _call_openrouter(payload_json: dict) -> httpx.Response:
                nonlocal model
                resp = await client.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload_json)
                if resp.status_code == 404 and model != _FALLBACK_MODEL:
                    logger.warning(f"Modelo '{model}' não encontrado no OpenRouter (404). Fallback para {_FALLBACK_MODEL}")
                    model = _FALLBACK_MODEL
                    payload_json["model"] = model
                    resp = await client.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload_json)
                return resp

            # ── ETAPA 1: Simular conversa Lead vs Agente ──
            sim_resp = await _call_openrouter({
                    "model": model,
                    "messages": [{"role": "user", "content": (
                        "Você é um 'Lead' (cliente em potencial) interessado nos serviços da empresa, "
                        "mas você é ocupado, direto e um pouco cético. Você está no WhatsApp.\n"
                        "Simule uma conversa de 4 turnos com o Agente de IA.\n\n"
                        "IMPORTANTE: O agente responde via WhatsApp. Avalie se as respostas são "
                        "naturais para WhatsApp (curtas, conversacionais, sem listas longas).\n\n"
                        f"--- PROMPT DO AGENTE ---\n{payload.prompt_text}\n\n"
                        "Formato:\nLead: [pergunta]\nAgente: [resposta]\n\n"
                        "Gere 4 turnos realistas."
                    )}]
                })
            if sim_resp.status_code != 200:
                err_detail = sim_resp.text[:200] if sim_resp.text else ""
                return {"success": False, "error": f"Falha na simulação (status {sim_resp.status_code}, modelo: {model}). {err_detail}"}
            transcript = sim_resp.json()["choices"][0]["message"]["content"]

            # ── ETAPA 2: Análise + lista de mudanças (SEM gerar prompt completo) ──
            analysis_resp = await _call_openrouter({"model": model, "max_tokens": 4000, "messages": [
                        {"role": "system", "content": (
                            "Você é um especialista sênior em Prompt Engineering para agentes de WhatsApp B2B.\n\n"
                            "Sua tarefa: analisar o prompt do agente com base em uma conversa simulada "
                            "e listar MUDANÇAS ESPECÍFICAS a serem feitas.\n\n"
                            "CONTEXTO CRÍTICO — Este agente opera via WhatsApp. As respostas devem seguir "
                            "boas práticas de comunicação no WhatsApp:\n"
                            "- NUNCA usar listas numeradas ou com bullets (1. 2. 3. / • • •)\n"
                            "- NUNCA usar formatação pesada de markdown (cabeçalhos ##, tabelas, etc.)\n"
                            "- Respostas CURTAS e conversacionais (máximo 2-3 frases por mensagem)\n"
                            "- Tom humano e natural, como se fosse uma pessoa real digitando\n"
                            "- Fazer UMA pergunta por vez, nunca bombardear o lead com informações\n"
                            "- Conduzir a conversa com perguntas estratégicas ao invés de despejar conteúdo\n"
                            "- Se o lead pedir detalhes, explicar de forma fluida em texto corrido, nunca em lista\n"
                            "- O objetivo é QUALIFICAR e ENGAJAR, não ser uma enciclopédia\n\n"
                            "Se o prompt atual não inclui essas restrições de formato, ADICIONE-AS como mudança.\n\n"
                            "NÃO gere o prompt completo. Apenas analise e liste as mudanças.\n\n"
                            "Retorne neste formato:\n\n"
                            "<analysis>\n"
                            "[Diagnóstico em markdown: objetivo do agente, o que a simulação revelou, "
                            "pontos fortes, pontos fracos, e o que precisa mudar]\n"
                            "</analysis>\n\n"
                            "<changes>\n"
                            "1. [SEÇÃO: nome] — [AÇÃO: adicionar/alterar/remover/reorganizar] — [O QUE: descrição detalhada da mudança]\n"
                            "2. ...\n"
                            "</changes>"
                        )},
                        {"role": "user", "content": (
                            f"PROMPT DO AGENTE:\n{payload.prompt_text}\n\n"
                            f"TRANSCRIÇÃO DO TESTE:\n{transcript}\n\n"
                            "Analise e liste as mudanças necessárias."
                        )}
                    ]
                }
            )

            if analysis_resp.status_code != 200:
                err_detail = analysis_resp.text[:200] if analysis_resp.text else ""
                return {"success": False, "error": f"IA Mestre falhou na análise (status {analysis_resp.status_code}, modelo: {model}). {err_detail}"}

            analysis_raw = analysis_resp.json()["choices"][0]["message"]["content"]
            analysis_text = _extract_tag(analysis_raw, "analysis")
            changes_text = _extract_tag(analysis_raw, "changes")

            if not analysis_text and not changes_text:
                logger.error(f"Delimitadores não encontrados na análise. Raw: {analysis_raw[:500]}")
                return {"success": False, "error": "Resposta malformada da IA Mestre."}

            # Se não há mudanças sugeridas, retorna apenas a análise
            if not changes_text or changes_text.lower().strip() in ["nenhuma", "nenhuma mudança", ""]:
                return {
                    "success": True,
                    "analysis": analysis_text,
                    "improved_prompt": "",
                    "simulation_transcript": transcript,
                }

            # ── ETAPA 3: Gerar DIFFS e aplicar programaticamente ──
            apply_resp = await _call_openrouter({
                    "model": model,
                    "max_tokens": 8000,
                    "messages": [
                        {"role": "system", "content": _DIFF_SYSTEM_PROMPT},
                        {"role": "user", "content": (
                            f"PROMPT ORIGINAL:\n{payload.prompt_text}\n\n"
                            f"MUDANÇAS A APLICAR:\n{changes_text}\n\n"
                            "Gere os patches no formato <<<FIND>>>...<<<REPLACE>>>...<<<END>>>."
                        )}
                    ]
                })

            if apply_resp.status_code != 200:
                return {"success": False, "error": f"IA Mestre falhou ao gerar diffs ({apply_resp.status_code})."}

            diffs_raw = apply_resp.json()["choices"][0]["message"]["content"].strip()
            improved = _apply_diffs(payload.prompt_text, diffs_raw)

            # Se nenhum diff foi aplicado, retorna o original com aviso
            if improved == payload.prompt_text:
                logger.warning("Nenhum diff foi aplicado com sucesso ao prompt.")
                return {
                    "success": True,
                    "analysis": analysis_text,
                    "improved_prompt": "",
                    "simulation_transcript": transcript,
                }

            return {
                "success": True,
                "analysis": analysis_text,
                "improved_prompt": improved,
                "simulation_transcript": transcript,
            }
    except Exception as e:
        logger.error(f"Erro no analisador de prompt: {e}", exc_info=True)
        return {"success": False, "error": "Erro interno do servidor ao tentar analisar."}
    finally:
        db.close()

class MasterChatRequest(BaseModel):
    original_prompt: str
    current_improved_prompt: str
    user_message: str
    chat_history: list = []

@router.post("/master-chat")
async def master_chat(payload: MasterChatRequest):
    """
    Chat iterativo com a IA Mestre. O usuário envia feedback sobre o agente
    e a Mestre revisa o prompt levando em consideração esse feedback.
    Usa abordagem de 2 etapas: primeiro gera resposta + mudanças, depois aplica ao prompt.
    """
    db = SessionLocal()
    try:
        settings = db.query(SystemSettings).first()
        if not settings or not settings.admin_openrouter_key:
            return {"success": False, "error": "Chave API do Administrador não configurada."}

        model = settings.admin_openrouter_model or "openai/gpt-4o"
        headers = _openrouter_headers(settings.admin_openrouter_key, "AxenWP Master Chat")

        # ── ETAPA 1: Gerar resposta conversacional + lista de mudanças ──
        system_prompt = (
            "Você é um especialista sênior em Prompt Engineering para agentes de WhatsApp B2B.\n\n"
            "Você já analisou o prompt de um agente e gerou uma sugestão de melhoria. "
            "Agora está em uma conversa com o dono do agente, que vai te dar feedback.\n\n"
            "LEMBRE-SE: Este agente opera via WhatsApp. Boas práticas obrigatórias:\n"
            "- NUNCA listas numeradas ou bullets nas respostas do agente\n"
            "- Respostas curtas e conversacionais (2-3 frases máximo)\n"
            "- Tom humano e natural, como uma pessoa digitando\n"
            "- Uma pergunta por vez, conduzir com perguntas estratégicas\n"
            "- Explicar de forma fluida em texto corrido, nunca em formato de lista\n\n"
            "Seu papel:\n"
            "- Responder de forma direta e consultiva ao feedback\n"
            "- Se o feedback exigir mudança, liste as mudanças específicas\n"
            "- Se for apenas dúvida/confirmação, responda sem listar mudanças\n\n"
            "Retorne neste formato:\n\n"
            "<response>\n[Sua resposta conversacional]\n</response>\n\n"
            "<changes>\n"
            "[Lista de mudanças específicas se necessário, ou VAZIO se não há mudanças]\n"
            "</changes>"
        )

        messages = [{"role": "system", "content": system_prompt}]
        context_msg = (
            f"PROMPT ATUAL DO AGENTE:\n{payload.current_improved_prompt or payload.original_prompt}"
        )
        messages.append({"role": "user", "content": context_msg})
        messages.append({"role": "assistant", "content": "Entendido. Tenho o contexto completo do prompt. Pode compartilhar seu feedback."})

        for turn in payload.chat_history:
            messages.append({"role": "user" if turn["from"] == "user" else "assistant", "content": turn["text"]})

        messages.append({"role": "user", "content": payload.user_message})

        _FALLBACK_MODEL = "openai/gpt-4o"

        async with httpx.AsyncClient(timeout=90.0) as client:
            async def _call_or(payload_json: dict) -> httpx.Response:
                nonlocal model
                r = await client.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload_json)
                if r.status_code == 404 and model != _FALLBACK_MODEL:
                    logger.warning(f"Modelo '{model}' não encontrado (404). Fallback para {_FALLBACK_MODEL}")
                    model = _FALLBACK_MODEL
                    payload_json["model"] = model
                    r = await client.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload_json)
                return r

            resp = await _call_or({
                    "model": model,
                    "max_tokens": 4000,
                    "messages": messages,
                }
            )

            if resp.status_code != 200:
                return {"success": False, "error": f"IA Mestre falhou ({resp.status_code})."}

            raw = resp.json()["choices"][0]["message"]["content"]
            response_text = _extract_tag(raw, "response") or raw.strip()
            changes_text = _extract_tag(raw, "changes")

            # Se não há mudanças, retorna só a resposta
            if not changes_text or changes_text.lower().strip() in ["vazio", "nenhuma", ""]:
                return {
                    "success": True,
                    "response": response_text,
                    "updated_prompt": "",
                }

            # ── ETAPA 2: Gerar DIFFS e aplicar programaticamente ──
            current_prompt = payload.current_improved_prompt or payload.original_prompt

            apply_resp = await _call_or({
                    "model": model,
                    "max_tokens": 8000,
                    "messages": [
                        {"role": "system", "content": _DIFF_SYSTEM_PROMPT},
                        {"role": "user", "content": (
                            f"PROMPT ORIGINAL:\n{current_prompt}\n\n"
                            f"MUDANÇAS A APLICAR:\n{changes_text}\n\n"
                            "Gere os patches no formato <<<FIND>>>...<<<REPLACE>>>...<<<END>>>."
                        )}
                    ]
                }
            )

            if apply_resp.status_code != 200:
                return {
                    "success": True,
                    "response": response_text,
                    "updated_prompt": "",
                }

            diffs_raw = apply_resp.json()["choices"][0]["message"]["content"].strip()
            updated_prompt = _apply_diffs(current_prompt, diffs_raw)

            return {
                "success": True,
                "response": response_text,
                "updated_prompt": updated_prompt,
            }

    except Exception as e:
        logger.error(f"Erro no master chat: {e}", exc_info=True)
        return {"success": False, "error": str(e)}
    finally:
        db.close()


@router.post("/{location_id}/test")
async def test_ai_agent(location_id: str, request: Request):
    """
    Endpoint manual para o Testador de Chat no dashboard.
    Recebe message e o prompt/modelo atual (mesmo sem salvar).
    """
    try:
        payload = await request.json()
        agent_data = payload.get("agent_data", {})
        prompt = agent_data.get("prompt")
        model = agent_data.get("model", "openai/gpt-4o")
        api_key = agent_data.get("api_key")
        user_message = payload.get("message")
        chat_history = payload.get("history", [])

        if not api_key:
            return {"success": False, "error": "A Chave API do Agente é necessária para testar."}

        messages = [{"role": "system", "content": prompt}]
        # Add history
        for h in chat_history[-5:]: # last 5
            messages.append({"role": "user" if h["from"] == "me" else "assistant", "content": h["text"]})

        # Current message
        messages.append({"role": "user", "content": user_message})

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "HTTP-Referer": "https://axenwp.com",
                    "X-Title": "AxenWP Agent Tester",
                },
                json={
                    "model": model,
                    "messages": messages
                }
            )

            if resp.status_code != 200:
                return {"success": False, "error": f"Erro na API do Agente: {resp.text}"}

            data = resp.json()
            ai_response = data["choices"][0]["message"]["content"]
            return {"success": True, "response": ai_response}
    except Exception as e:
        logger.error(f"Erro no chat tester: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


@router.post("/{location_id}/form-data")
async def save_form_data(location_id: str, request: Request):
    """Salva form_data editado e opcionalmente regenera o prompt via IA Mestre."""
    from admin.dashboard import verify_admin
    from fastapi import Cookie
    admin_session = request.cookies.get("admin_session")
    if not verify_admin(admin_session):
        return {"success": False, "error": "Não autenticado."}

    body = await request.json()
    form_data = body.get("form_data", {})
    regenerate = body.get("regenerate", False)
    channel = body.get("channel", "whatsapp")

    db = SessionLocal()
    try:
        agent = db.query(AIAgent).filter(
            AIAgent.location_id == location_id,
            AIAgent.channel == channel,
        ).first()
        if not agent:
            return {"success": False, "error": "Agente não encontrado."}

        agent.form_data = form_data
        if agent.name and form_data.get("agent_name"):
            agent.name = form_data["agent_name"]

        if regenerate:
            settings = db.query(SystemSettings).first()
            if not settings or not settings.admin_openrouter_key:
                db.commit()
                return {"success": False, "error": "IA Mestre não configurada (System Settings)."}

            fd = form_data
            company_context = f"""
INFORMAÇÕES DA EMPRESA:
- Nome: {fd.get('company_name', '')}
- Segmento: {fd.get('industry', '')}
- Descrição: {fd.get('company_description', '')}
- Público-alvo: {fd.get('target_audience', '') or 'Não especificado'}
- Website: {fd.get('website', '') or 'Não informado'}
- Instagram: {fd.get('instagram', '') or 'Não informado'}

PRODUTOS/SERVIÇOS:
{fd.get('products_services', '')}

DIFERENCIAIS:
{fd.get('differentials', '') or 'Não informado'}

PERGUNTAS FREQUENTES (FAQ):
{fd.get('faq', '') or 'Nenhuma informada'}

CONFIGURAÇÃO DO AGENTE:
- Nome do agente: {fd.get('agent_name', '')}
- Tom de voz: {fd.get('tone', '') or 'Não especificado'}
- Horário de funcionamento: {fd.get('business_hours', '') or 'Não informado'}
- Contatos para transferência: {fd.get('contact_info', '') or 'Não informado'}

OBJETIVO PRINCIPAL:
{fd.get('agent_goal', '')}

RESTRIÇÕES (o que NÃO fazer):
{fd.get('restrictions', '') or 'Nenhuma especificada'}

PERGUNTAS QUALIFICATÓRIAS (para qualificar o lead antes de transferir):
{fd.get('qualification_questions', '') or 'Nenhuma definida'}

INFORMAÇÕES ADICIONAIS:
{fd.get('extra_info', '') or 'Nenhuma'}
""".strip()

            api_key = settings.admin_openrouter_key
            model = settings.admin_openrouter_model or "openai/gpt-4o"

            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "HTTP-Referer": "https://axenwp.com",
                        "X-Title": "AxenWP Prompt Generator",
                    },
                    json={
                        "model": model,
                        "max_tokens": 6000,
                        "messages": [
                            {"role": "system", "content": (
                                "Você é um especialista sênior em Prompt Engineering para agentes de IA de WhatsApp.\n\n"
                                "Sua tarefa: receber informações sobre uma empresa e criar um PROMPT DE SISTEMA completo, "
                                "detalhado e profissional para o agente de IA que vai atender os clientes dessa empresa via WhatsApp.\n\n"
                                "O prompt deve:\n"
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
                    logger.error(f"Erro OpenRouter ao regenerar prompt: {resp.status_code} — {resp.text}")
                    db.commit()
                    return {"success": False, "error": "Erro ao gerar prompt com IA Mestre."}

                generated_prompt = resp.json()["choices"][0]["message"]["content"]
                agent.prompt = generated_prompt

        db.commit()
        return {
            "success": True,
            "prompt": agent.prompt if regenerate else None,
        }
    except Exception as e:
        logger.error(f"Erro ao salvar form_data: {e}", exc_info=True)
        db.rollback()
        return {"success": False, "error": str(e)}
    finally:
        db.close()
