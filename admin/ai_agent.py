import logging
import re
from typing import Optional
from fastapi import APIRouter, Body, Depends, Form, HTTPException, Request
import httpx
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import json

from data.database import get_db, SessionLocal
from data.models import Tenant, AIAgent, SystemSettings, ChatHistory, QualifiedLead, OnboardingSubmission
from auth.token_manager import token_manager
from utils.validators import is_valid_location_id
from services.ghl_service import ghl_service
from datetime import datetime, timezone
from pydantic import BaseModel

# TODAS as rotas deste router exigem admin. Era o único router sem barreira: 25
# rotas, nenhuma checando o cookie, e o GET do agente devolvia api_key,
# elevenlabs_api_key e o prompt inteiro em texto puro para quem tivesse a URL.
# Aplicar no APIRouter (e não rota a rota) faz a proteção valer por construção —
# rota nova nasce fechada, sem depender de ninguém lembrar.
from admin.dashboard import require_admin  # noqa: E402

def _like_prefix(prefix: str) -> str:
    """
    Escapa curingas para usar um prefixo literal em LIKE.

    `location_id` CONTÉM underline (ex.: `wp_9fe4c6ef7915`), e `_` é curinga de um
    caractere em SQL — sem escapar, o filtro de um tenant casa com o session_id de
    outro cujo id difira só naquela posição. Vazamento silencioso entre tenants.
    Use sempre com `escape="\\"`.
    """
    return prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


router = APIRouter(
    prefix="/admin/agents",
    tags=["admin_agents"],
    dependencies=[Depends(require_admin)],
)
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
    agent_engine: str = Form("claude"),
    anthropic_model: Optional[str] = Form(None),
    anthropic_api_key: Optional[str] = Form(None),
    tts_provider: str = Form("elevenlabs"),
    elevenlabs_api_key: Optional[str] = Form(None),
    elevenlabs_voice_id: Optional[str] = Form(None),
    elevenlabs_speed: float = Form(1.0),
    elevenlabs_stability: float = Form(0.5),
    elevenlabs_similarity: float = Form(0.75),
    fishaudio_api_key: Optional[str] = Form(None),
    fishaudio_voice_id: Optional[str] = Form(None),
    fishaudio_model: str = Form("s1"),
    fishaudio_speed: float = Form(1.0),
    fishaudio_temperature: float = Form(0.7),
    fishaudio_top_p: float = Form(0.7),
    fishaudio_normalize_loudness: bool = Form(True),
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
    from utils.agent_validators import AgentSettingsInput

    # Validação centralizada (clamps + parsing de qualification_fields)
    try:
        validated = AgentSettingsInput(
            name=name,
            prompt=prompt,
            model=model,
            api_key=api_key,
            agent_engine=agent_engine,
            anthropic_model=anthropic_model,
            anthropic_api_key=anthropic_api_key,
            tts_provider=tts_provider,
            elevenlabs_api_key=elevenlabs_api_key,
            elevenlabs_voice_id=elevenlabs_voice_id,
            elevenlabs_speed=elevenlabs_speed,
            elevenlabs_stability=elevenlabs_stability,
            elevenlabs_similarity=elevenlabs_similarity,
            fishaudio_api_key=fishaudio_api_key,
            fishaudio_voice_id=fishaudio_voice_id,
            fishaudio_model=fishaudio_model,
            fishaudio_speed=fishaudio_speed,
            fishaudio_temperature=fishaudio_temperature,
            fishaudio_top_p=fishaudio_top_p,
            fishaudio_normalize_loudness=fishaudio_normalize_loudness,
            groq_api_key=groq_api_key,
            is_active=is_active,
            debounce_seconds=debounce_seconds,
            qualification_enabled=qualification_enabled,
            qualification_pipeline_id=qualification_pipeline_id,
            qualification_stage_id=qualification_stage_id,
            qualification_fields=qualification_fields,
            qualification_summary_prompt=qualification_summary_prompt,
            channel=channel,
        )
    except Exception as e:
        logger.error(f"Validação falhou ao salvar Agente IA: {e}")
        return RedirectResponse(url="/admin/dashboard?err=Dados+invalidos", status_code=303)

    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter(Tenant.location_id == location_id).first()
        if not tenant:
            logger.error(f"Tenant {location_id} não encontrado ao tentar salvar Agente IA.")
            return RedirectResponse(url="/admin/dashboard?err=Tenant+não+encontrado", status_code=303)

        # Busca agente existente ou cria novo (escopo: location_id + channel)
        agent = db.query(AIAgent).filter(
            AIAgent.location_id == location_id,
            AIAgent.channel == validated.channel,
        ).first()

        if not agent:
            agent = AIAgent(location_id=location_id, channel=validated.channel)
            db.add(agent)

        agent.name = validated.name
        agent.prompt = validated.prompt
        agent.model = validated.model
        agent.api_key = validated.api_key
        agent.agent_engine = validated.agent_engine
        agent.anthropic_model = validated.anthropic_model
        # Só sobrescreve a chave Anthropic se veio preenchida (senão herda a do admin).
        if (anthropic_api_key or "").strip():
            agent.anthropic_api_key = anthropic_api_key.strip()
        agent.tts_provider = validated.tts_provider
        agent.elevenlabs_api_key = validated.elevenlabs_api_key
        agent.elevenlabs_voice_id = validated.elevenlabs_voice_id
        agent.elevenlabs_speed = validated.elevenlabs_speed
        agent.elevenlabs_stability = validated.elevenlabs_stability
        agent.elevenlabs_similarity = validated.elevenlabs_similarity
        agent.fishaudio_api_key = validated.fishaudio_api_key
        agent.fishaudio_voice_id = validated.fishaudio_voice_id
        agent.fishaudio_model = validated.fishaudio_model
        agent.fishaudio_speed = validated.fishaudio_speed
        agent.fishaudio_temperature = validated.fishaudio_temperature
        agent.fishaudio_top_p = validated.fishaudio_top_p
        agent.fishaudio_normalize_loudness = validated.fishaudio_normalize_loudness
        agent.groq_api_key = validated.groq_api_key
        agent.is_active = validated.is_active
        agent.debounce_seconds = validated.debounce_seconds

        # Qualificação de leads
        agent.qualification_enabled = validated.qualification_enabled
        agent.qualification_pipeline_id = validated.qualification_pipeline_id
        agent.qualification_stage_id = validated.qualification_stage_id
        agent.qualification_summary_prompt = validated.qualification_summary_prompt
        agent.qualification_fields = (
            [f.model_dump(exclude_none=True) for f in validated.qualification_fields]
            if validated.qualification_fields
            else None
        )

        agent.updated_at = datetime.now(timezone.utc)

        db.commit()
        logger.info(f"Configurações do Agente IA atualizadas para o Tenant {location_id}.")

        # Snapshot da versão salva no histórico
        try:
            from services.prompt_history import snapshot_prompt
            snapshot_prompt(
                location_id=location_id,
                channel=validated.channel,
                prompt=agent.prompt,
                source="manual_save",
                agent_id=agent.id,
                form_data_snapshot=agent.form_data,
            )
        except Exception as e_snap:
            logger.warning(f"Falha snapshot prompt: {e_snap}")

        return RedirectResponse(url="/admin/dashboard?msg=Agente+IA+atualizado+com+sucesso", status_code=303)

    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao salvar Agente IA para o tenant {location_id}: {e}", exc_info=True)
        return RedirectResponse(url="/admin/dashboard?err=Erro+ao+salvar+Agente+IA", status_code=303)
    finally:
        db.close()

def _agente_da_tela(db, location_id: str, channel: Optional[str] = None):
    """
    O agente que uma tela POR INSTÂNCIA deve usar.

    Estas telas (conversas, progresso de qualificação, tester) são de instância, não
    de canal — mas leem config que é POR AGENTE. Com um agente só, tanto faz; com
    dois, o `.first()` sem ordem escolhia um arbitrário e a tela mostrava o campo de
    qualificação do agente errado.

    Enquanto não há seletor de agente nessas telas, a regra é: usa o canal pedido se
    vier; senão, o de MENOR id — determinístico, e é o primeiro que o operador criou.
    """
    q = db.query(AIAgent).filter(AIAgent.location_id == location_id)
    if channel:
        q = q.filter(AIAgent.channel == channel)
    agente = q.order_by(AIAgent.id).first()

    # Alias resolvido, como `ai_service` faz antes de usar a config. A linha-espelho
    # criada por `link_channel` tem só name/prompt/linked_to_channel: sem resolver, a
    # tela lia `qualification_fields=None` de um canal que, no runtime, usa os campos
    # reais do agente apontado — e mostrava "sem qualificação" para um agente que
    # qualifica.
    alvo = getattr(agente, "linked_to_channel", None)
    if agente is not None and alvo:
        real = (
            db.query(AIAgent)
            .filter(AIAgent.location_id == location_id, AIAgent.channel == alvo)
            .order_by(AIAgent.id)
            .first()
        )
        if real is not None:
            return real
    return agente


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
                    "linked_to_channel": a.linked_to_channel,
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
                "qualification_enabled": bool(agent.qualification_enabled),
                "qualification_pipeline_id": agent.qualification_pipeline_id or "",
                "qualification_stage_id": agent.qualification_stage_id or "",
                "qualification_fields": agent.qualification_fields or [],
                "qualification_summary_prompt": agent.qualification_summary_prompt or "",
                "linked_to_channel": agent.linked_to_channel,
            },
        }
    except Exception as e:
        logger.error(f"Erro ao buscar agente: {e}")
        return {"success": False, "error": str(e)}
    finally:
        db.close()


@router.delete("/{location_id}/agent")
async def delete_agent_by_channel(location_id: str, channel: str, agent_id: Optional[int] = None):
    """
    Remove UM agente. `agent_id` endereça direto; `channel` continua aceito.

    Antes isto era `.filter(...).delete()` — delete de CONJUNTO, não de registro.
    Hoje a trava `UNIQUE(location_id, channel)` garante que o conjunto tem no
    máximo um elemento, então o estrago é invisível; no dia em que houver dois
    agentes no mesmo canal, um clique em "remover" apaga os dois. Deletar é
    irreversível e não avisa — é o pior lugar para deixar essa forma de query.
    """
    if channel == "whatsapp" and agent_id is None:
        return {"success": False, "error": "Nao e permitido deletar o agente do canal principal (whatsapp)."}
    db = SessionLocal()
    try:
        q = db.query(AIAgent).filter(AIAgent.location_id == location_id)
        q = q.filter(AIAgent.id == agent_id) if agent_id is not None else q.filter(AIAgent.channel == channel)
        agente = q.first()
        if agente is None:
            # Sucesso com `deleted: 0`, não erro. Remover uma aba de canal ainda NÃO
            # SALVA (que existe só na tela) caía aqui: o front tratava como falha,
            # mostrava alerta e não recarregava as abas — a aba fantasma ficava na
            # tela com `ai_channel` apontando para ela. Não há nada a remover, e isso
            # não é uma falha.
            return {"success": True, "deleted": 0}
        if agente.channel == "whatsapp":
            # O guard vale pelo canal do agente ENCONTRADO, não pelo que o
            # chamador disse — senão passar `agent_id` contornaria a regra.
            return {"success": False, "error": "Nao e permitido deletar o agente do canal principal (whatsapp)."}

        rotulo = f"{agente.name} ({agente.channel}, id={agente.id})"
        db.delete(agente)
        db.commit()
        logger.info(f"Agente {rotulo} removido para tenant {location_id}.")
        return {"success": True, "deleted": 1}
    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao remover agente: {e}")
        return {"success": False, "error": str(e)}
    finally:
        db.close()


@router.post("/{location_id}/link-channel")
async def link_channel_to_existing(location_id: str, request: Request):
    """
    Cria/atualiza um canal como ALIAS de um agente existente.
    Body: {"channel": "telegram", "linked_to": "whatsapp"}
    O canal vinculado compartilha prompt, chaves, qualificação, form_data.
    """
    body = await request.json()
    channel = (body.get("channel") or "").strip()
    linked_to = (body.get("linked_to") or "").strip()
    if not channel or not linked_to:
        return {"success": False, "error": "channel e linked_to são obrigatórios."}
    if channel == linked_to:
        return {"success": False, "error": "Não pode vincular um canal a ele mesmo."}

    db = SessionLocal()
    try:
        target = db.query(AIAgent).filter(
            AIAgent.location_id == location_id,
            AIAgent.channel == linked_to,
        ).first()
        if not target:
            return {"success": False, "error": f"Agente do canal {linked_to} não existe."}
        if getattr(target, "linked_to_channel", None):
            return {"success": False, "error": f"Canal {linked_to} já é um alias. Vincule ao canal raiz."}

        agent = db.query(AIAgent).filter(
            AIAgent.location_id == location_id,
            AIAgent.channel == channel,
        ).first()
        if agent:
            agent.linked_to_channel = linked_to
        else:
            agent = AIAgent(
                location_id=location_id,
                channel=channel,
                name=target.name or "Agente",
                prompt="(alias — usa as configs do canal vinculado)",
                linked_to_channel=linked_to,
            )
            db.add(agent)

        db.commit()
        logger.info(f"Canal {channel} vinculado a {linked_to} para tenant {location_id}")
        return {"success": True, "channel": channel, "linked_to": linked_to}
    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao vincular canal: {e}", exc_info=True)
        return {"success": False, "error": str(e)}
    finally:
        db.close()


@router.post("/{location_id}/unlink-channel")
async def unlink_channel(location_id: str, request: Request):
    """Remove o vínculo de um canal alias (volta a ser independente, mas vazio)."""
    body = await request.json()
    channel = (body.get("channel") or "").strip()
    if not channel or channel == "whatsapp":
        return {"success": False, "error": "Canal inválido."}

    db = SessionLocal()
    try:
        agent = db.query(AIAgent).filter(
            AIAgent.location_id == location_id,
            AIAgent.channel == channel,
        ).first()
        if not agent:
            return {"success": False, "error": "Canal não encontrado."}
        agent.linked_to_channel = None
        db.commit()
        return {"success": True}
    finally:
        db.close()


@router.get("/{location_id}/inherit-keys")
async def get_inherit_keys(location_id: str):
    """Retorna as chaves do agente WhatsApp para o front pré-preencher outro canal."""
    db = SessionLocal()
    try:
        wa = db.query(AIAgent).filter(
            AIAgent.location_id == location_id,
            AIAgent.channel == "whatsapp",
        ).first()
        if not wa:
            return {"success": False, "error": "Nenhum agente WhatsApp encontrado para herdar chaves."}
        return {
            "success": True,
            "api_key": wa.api_key or "",
            "model": wa.model or "openai/gpt-4o",
            "groq_api_key": wa.groq_api_key or "",
            "tts_provider": wa.tts_provider or "elevenlabs",
            "elevenlabs_api_key": wa.elevenlabs_api_key or "",
            "elevenlabs_voice_id": wa.elevenlabs_voice_id or "",
            "fishaudio_api_key": wa.fishaudio_api_key or "",
            "fishaudio_voice_id": wa.fishaudio_voice_id or "",
            "fishaudio_model": wa.fishaudio_model or "s1",
        }
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
async def get_conversations(location_id: str, offset: int = 0, limit: int = 20,
                            channel: Optional[str] = None):
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
            .filter(ChatHistory.session_id.like(f"{_like_prefix(prefix)}%", escape="\\"))
            .group_by(ChatHistory.session_id)
            .order_by(desc("last_msg"))
            .offset(offset)
            .limit(limit)
        )

        results = contacts_q.all()
        total = (
            db.query(func.count(func.distinct(ChatHistory.session_id)))
            .filter(ChatHistory.session_id.like(f"{_like_prefix(prefix)}%", escape="\\"))
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

        # Campos de qualificação do agente (ver `_agente_da_tela` sobre a escolha).
        agent = _agente_da_tela(db, location_id, channel)
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
async def get_qualification_progress(location_id: str, phone: str,
                                     channel: Optional[str] = None):
    """
    Retorna o progresso de qualificação de um lead.
    - Se qualificado: retorna os dados confirmados do QualifiedLead
    - Se em andamento: retorna o cache de progresso extraído pelo AI
    """
    from services.qualification_engine import qual_progress_cache as _qual_progress_cache
    db = SessionLocal()
    try:
        agent = _agente_da_tela(db, location_id, channel)
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
    Remove a qualificação de um lead, RELIGA a IA e limpa o cache de progresso.
    Se clear_history=true, apaga também o histórico de conversa (necessário para
    evitar que a IA re-qualifique imediatamente ao ler as mensagens anteriores).

    O religar virou explícito. Antes ele era efeito colateral de apagar a linha de
    `qualified_leads` — que era o gate. Com o interruptor em tabela própria, apagar
    a qualificação sozinho deixaria a conversa muda para sempre, e este endpoint
    era o único caminho que o operador tinha.
    """
    import asyncio as _asyncio

    from services import ai_gate
    from services.qualification_engine import qual_progress_cache as _qual_progress_cache

    religados = await _asyncio.to_thread(
        ai_gate.religar_todos_os_canais, location_id, phone, "operador"
    )

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
            f"Reset para {phone} @ {location_id}: qual={deleted_qual}, "
            f"history={deleted_history}, canais religados={religados}"
        )
        return {"success": True, "deleted_qual": deleted_qual,
                "deleted_history": deleted_history, "ia_religada_em": religados}
    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao resetar qualificação: {e}")
        return {"success": False, "error": str(e)}
    finally:
        db.close()


@router.post("/{location_id}/conversations/{phone}/ai")
async def definir_estado_da_ia(location_id: str, phone: str, payload: dict = Body(...)):
    """
    Liga ou desliga a IA numa conversa. O botão que não existia.

    Até aqui, pausar uma conversa só era possível de dois jeitos torto: a IA se
    pausar sozinha (qualificando ou escalando), ou o operador desligar o agente
    INTEIRO daquele canal — o que cala os outros trezentos leads junto.

    `channel` omitido vale para todos os canais da conversa: quem clica no painel
    está falando da pessoa, não do WhatsApp dela.
    """
    import asyncio as _asyncio

    from services import ai_gate

    if not is_valid_location_id(location_id):
        return {"success": False, "error": "location_id inválido."}

    enabled = bool(payload.get("enabled"))
    channel = (payload.get("channel") or "").strip() or None
    motivo = (payload.get("motivo") or "").strip() or (None if enabled else ai_gate.OPERADOR)
    minutos = payload.get("minutos")

    if enabled and not channel:
        n = await _asyncio.to_thread(
            ai_gate.religar_todos_os_canais, location_id, phone, "operador"
        )
        return {"success": True, "enabled": True, "canais": n}

    if not channel:
        # Pausar sem canal precisaria adivinhar em quais canais criar linha. O
        # painel sabe de qual conversa está falando; exigir é melhor que chutar.
        return {"success": False, "error": "Informe o canal para pausar (whatsapp | telegram)."}

    ok = await _asyncio.to_thread(
        ai_gate.definir,
        location_id=location_id, channel=channel, contact_ref=phone,
        enabled=enabled, motivo=motivo, mudado_por="operador",
        minutos=int(minutos) if minutos else None,
    )
    return {"success": ok, "enabled": enabled, "channel": channel}


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


FISH_API = "https://api.fish.audio"


def _fish_voz(item: dict) -> dict:
    """Um modelo do Fish Audio no formato que a tela consome."""
    return {
        "voice_id": item.get("_id"),
        "name": item.get("title") or "Sem título",
        "languages": item.get("languages") or [],
        "state": item.get("state"),
        "autor": ((item.get("author") or {}).get("nickname")
                  if isinstance(item.get("author"), dict) else None),
    }


@router.get("/fishaudio/voices")
async def get_fishaudio_voices(
    api_key: str,
    escopo: str = "minhas",
    busca: str = "",
    idioma: str = "",
):
    """
    Lista modelos de voz do Fish Audio.

    `escopo="minhas"` manda `self=true` e traz só o que a conta treinou/clonou —
    era o único comportamento possível até 2026-08-19, e é por isso que a tela
    mostrava "0 vozes carregadas" para quem nunca treinou nada: as vozes públicas
    (a maior parte do que se usa de fato) ficavam invisíveis, sem nenhuma pista de
    que existiam.

    `escopo="publicas"` omite o `self` — o default da própria API é `false`.
    """
    if not api_key:
        raise HTTPException(status_code=400, detail="API Key do Fish Audio é obrigatória.")

    params: dict = {"page_size": 100}
    if escopo != "publicas":
        params["self"] = "true"
    else:
        # No acervo público a ordem importa: sem isto vem o que a API achar
        # "relevante" para busca vazia, que não é o que o operador espera ver.
        params["sort_by"] = "task_count"
    if busca.strip():
        params["title"] = busca.strip()
    if idioma.strip():
        params["language"] = idioma.strip()

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{FISH_API}/model",
                headers={"Authorization": f"Bearer {api_key}"},
                params=params,
            )
            if response.status_code != 200:
                raise HTTPException(
                    status_code=400,
                    detail=f"Fish Audio retornou {response.status_code}: {response.text[:200]}",
                )
            data = response.json()
            voices = [_fish_voz(i) for i in data.get("items", []) if i.get("_id")]
            return {
                "success": True,
                "voices": voices,
                "total": data.get("total", len(voices)),
                "escopo": escopo,
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao buscar vozes no Fish Audio: {e}")
        raise HTTPException(status_code=500, detail="Erro interno ao consultar Fish Audio.")


class _FishModeloPayload(BaseModel):
    api_key: str
    model_id: str


def _fish_id_da_entrada(bruto: str) -> str:
    """
    Extrai o id do modelo de um id cru, de um UUID com hífen, ou da URL colada.

    O id do Fish tem 32 hex (medido: 60 de 60 modelos amostrados em 2026-08-19).
    A faixa 24-36 é folga — a versão anterior fixava 24, por eu ter suposto
    ObjectId do Mongo, e o `match` recortava os primeiros 24 de um id de 32,
    devolvendo um id truncado que a API então recusava como inexistente.
    """
    t = (bruto or "").strip().replace("-", "")
    achado = re.search(r"[0-9a-f]{24,36}", t, re.I)
    return achado.group(0) if achado else t


@router.post("/fishaudio/model")
async def resolve_fishaudio_model(payload: _FishModeloPayload):
    """
    Confere UM modelo do Fish Audio pelo id e devolve o nome.

    Existe porque a tela só oferecia um `<select>` montado a partir da lista da
    própria conta: um id colado do site do Fish Audio não tinha por onde entrar,
    mesmo o runtime aceitando qualquer string (`reference_id` em audio_handler).

    Validar antes de salvar é o ponto. Sem isto o operador grava um id errado e só
    descobre quando o lead recebe silêncio no lugar do áudio — falha que aparece
    longe, em produção, e não na tela onde foi cometida.

    **POST, e não GET como a rota irmã de listagem, porque a chave vai no corpo.**
    Em query string ela entra no log de acesso do proxy e no histórico do
    navegador. (A rota de listagem continua GET; trocá-la é mudança à parte.)
    """
    api_key = (payload.api_key or "").strip()
    model_id = (payload.model_id or "").strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="API Key do Fish Audio é obrigatória.")
    if not model_id:
        raise HTTPException(status_code=400, detail="Informe o ID do modelo.")
    # Formato MEDIDO contra a API, não suposto: 60 de 60 modelos amostrados em
    # 2026-08-19 têm 32 hex (UUID sem hífen). A primeira versão desta linha exigia
    # 24 — eu assumi ObjectId do Mongo — e teria recusado TODO id real; os testes
    # concordaram porque o dado falso deles nasceu da mesma suposição.
    #
    # A faixa 24-36 é folga deliberada: o objetivo aqui é só não mandar para fora
    # coisa que obviamente não é id (uma frase, uma URL quebrada), levando a chave
    # junto. Quem decide se o id existe é a Fish, logo abaixo.
    # A extração também mora aqui, e não só no JS, para as duas camadas concordarem:
    # a tela aceita a URL do modelo colada inteira, e um backend que a recusasse
    # devolveria "ID inválido" para uma entrada que o operador vê como correta —
    # sempre que a extração do JS falhasse. Foi exatamente o que aconteceu quando o
    # extrator truncava ids de 32 em 24: o erro apontava para o lugar errado.
    model_id = _fish_id_da_entrada(model_id)
    if not re.fullmatch(r"[0-9a-f]{24,36}", model_id, re.I):
        raise HTTPException(
            status_code=400,
            detail=("ID inválido. É a sequência hexadecimal no fim da URL do modelo "
                    "no site do Fish Audio (32 caracteres)."),
        )

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"{FISH_API}/model/{model_id}",
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except Exception as e:
        logger.error(f"Erro ao consultar modelo Fish Audio: {e}")
        raise HTTPException(status_code=500, detail="Não consegui falar com o Fish Audio.")

    # Os três casos são distinguíveis na API e merecem mensagens distintas: dizer
    # "erro" para os três faria o operador conferir a chave quando o problema é o
    # id, e vice-versa.
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Nenhum modelo com esse ID.")
    if r.status_code in (401, 403):
        raise HTTPException(
            status_code=403,
            detail="Sua chave não tem acesso a esse modelo (ele é privado de outra conta?).",
        )
    if r.status_code != 200:
        raise HTTPException(
            status_code=400,
            detail=f"Fish Audio retornou {r.status_code}: {r.text[:160]}",
        )

    voz = _fish_voz(r.json() or {})
    # A API pode responder 200 sem `_id` se mudar de formato; assumir o id pedido
    # seria gravar no banco algo que nunca foi confirmado.
    voz["voice_id"] = voz.get("voice_id") or model_id
    return {"success": True, "voice": voz}


# ── Helpers para chamadas OpenRouter ──

def _openrouter_headers(api_key: str, title: str = "MilloChat") -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://millochat.com",
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
        headers = _openrouter_headers(settings.admin_openrouter_key, "MilloChat Prompt Analyzer")
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
        headers = _openrouter_headers(settings.admin_openrouter_key, "MilloChat Master Chat")

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
                    "HTTP-Referer": "https://millochat.com",
                    "X-Title": "MilloChat Agent Tester",
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

            # Aplica os mesmos guardrails do fluxo real de produção
            from utils.guardrails import strip_emojis, contains_forbidden_phrase, contains_placeholder
            ai_response = strip_emojis(ai_response)
            ph = contains_placeholder(ai_response)
            if ph:
                ai_response = (
                    ai_response
                    + f"\n\n⚠️ [debug: placeholder não resolvido detectado: {ph}. Em produção seria regenerado. Clique em Regenerar Prompt na aba Cadastro.]"
                )

            # Detecta modo do agente pelo form_data salvo
            agent_mode = "inbound"
            try:
                db = SessionLocal()
                agent_db = _agente_da_tela(db, location_id, agent_data.get("channel"))
                if agent_db and agent_db.form_data:
                    agent_mode = agent_db.form_data.get("agent_type", "inbound")
                db.close()
            except Exception:
                pass

            if agent_mode == "outbound" and contains_forbidden_phrase(ai_response, "outbound"):
                ai_response = (
                    ai_response
                    + "\n\n⚠️ [debug: resposta contém frase proibida outbound — em produção seria regenerada automaticamente. Clique em Regenerar Prompt na aba Cadastro.]"
                )

            return {"success": True, "response": ai_response}
    except Exception as e:
        logger.error(f"Erro no chat tester: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


@router.post("/{location_id}/treinar")
async def treinar_agente(location_id: str, request: Request):
    """
    Ciclo fechado: pedido do operador → ajuste → TESTE → evidência.

    Corpo: {"pedido": "...", "channel": "whatsapp", "publicar": false}

    O que muda em relação a `improve-prompt`: aqui nada é publicado sem prova. A
    Mestre devolve o prompt candidato E o caso que verifica o pedido; rodamos o
    candidato e o ATUAL contra os mesmos casos (mais o roteiro de regressão) e
    devolvemos o comparativo. Publicar é uma segunda chamada, com `publicar=true`
    e o candidato em mãos.

    A separação é o ponto: "aplicar melhoria" reescrevia 6,6 mil caracteres de
    produção e o operador clicava e torcia. Agora ele decide com o número na frente.
    """
    from services.mestre_ciclo import CicloIndisponivel, propor_ajuste, verificar

    if not is_valid_location_id(location_id):
        return {"success": False, "error": "location_id inválido."}
    try:
        corpo = await request.json()
    except Exception:
        corpo = {}

    pedido = (corpo.get("pedido") or "").strip()
    canal = (corpo.get("channel") or "whatsapp").strip()
    if len(pedido) < 10:
        return {"success": False, "error": "Descreva o que o agente deve passar a fazer."}

    db = SessionLocal()
    try:
        agente = (
            db.query(AIAgent)
            .filter(AIAgent.location_id == location_id, AIAgent.channel == canal)
            .first()
        )
        if agente is None:
            return {"success": False, "error": "Agente não encontrado neste canal."}
        db.expunge(agente)   # usado fora da sessão pela sonda
    finally:
        db.close()

    try:
        proposta = await propor_ajuste(agente, pedido)
    except CicloIndisponivel as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.error(f"[TREINAR] {location_id}: {type(e).__name__}: {e}")
        return {"success": False, "error": "Não consegui montar o ajuste agora."}

    if proposta["ja_atendido"] and proposta["prompt_novo"].strip() == (agente.prompt or "").strip():
        # Resposta legítima: dizer "já faz isso" é melhor que inventar mudança.
        return {"success": True, "ja_atendido": True, "resumo": proposta["resumo"],
                "casos": proposta["casos"], "verificacao": None}

    try:
        verif = await verificar(agente, proposta["prompt_novo"], proposta["casos"])
    except Exception as e:
        logger.error(f"[TREINAR] verificação falhou em {location_id}: {type(e).__name__}: {e}")
        return {"success": False, "error": "O ajuste saiu, mas não consegui testá-lo. Nada foi publicado."}

    logger.info(
        f"[TREINAR] {location_id}/{canal}: {verif['resumo']} · "
        f"recomendação={verif['recomendacao']}"
    )
    return {
        "success": True,
        "ja_atendido": False,
        "resumo": proposta["resumo"],
        "prompt_novo": proposta["prompt_novo"],
        "tamanho_antes": len(agente.prompt or ""),
        "tamanho_depois": len(proposta["prompt_novo"]),
        "verificacao": verif,
    }


@router.post("/{location_id}/improve-prompt")
async def improve_prompt(location_id: str, request: Request):
    """
    Diagnostica ou melhora o prompt do agente considerando contexto rico:
    form_data + prompt atual + histórico de conversas + feedback do operador.

    Body:
      {
        "mode": "diagnose" | "apply",
        "channel": "whatsapp" (default),
        "feedback": "texto opcional do operador",
        "test_history": [{role, content}, ...]  # opcional, do simulador
      }
    """
    from admin.dashboard import verify_admin
    admin_session = request.cookies.get("admin_session")
    if not verify_admin(admin_session):
        return {"success": False, "error": "Não autenticado."}

    body = await request.json()
    mode = body.get("mode", "diagnose")
    channel = body.get("channel", "whatsapp")
    feedback = (body.get("feedback") or "").strip()
    test_history = body.get("test_history") or []

    if mode not in ("diagnose", "apply"):
        return {"success": False, "error": "mode inválido."}

    db = SessionLocal()
    try:
        agent = db.query(AIAgent).filter(
            AIAgent.location_id == location_id,
            AIAgent.channel == channel,
        ).first()
        if not agent:
            return {"success": False, "error": "Agente não encontrado."}

        # Se é alias, opera no agente raiz
        if getattr(agent, "linked_to_channel", None):
            root = db.query(AIAgent).filter(
                AIAgent.location_id == location_id,
                AIAgent.channel == agent.linked_to_channel,
            ).first()
            if root:
                agent = root

        settings_row = db.query(SystemSettings).first()
        if not settings_row or not settings_row.admin_openrouter_key:
            return {"success": False, "error": "IA Mestre não configurada (System Settings)."}

        # Combina histórico salvo do banco com histórico do simulador (test)
        # Prioriza conversas reais; complementa com simulador se vazio
        from data.models import ChatHistory
        real_history = []
        try:
            session_prefix = f"{location_id}_"
            rows = (
                db.query(ChatHistory)
                .filter(ChatHistory.session_id.like(f"{_like_prefix(session_prefix)}%", escape="\\"))
                .order_by(ChatHistory.created_at.desc())
                .limit(40)
                .all()
            )
            rows.reverse()
            for r in rows:
                real_history.append({
                    "role": "human" if r.message_type == "human" else "ai",
                    "content": r.content,
                })
        except Exception as e:
            logger.warning(f"Falha ao ler chat_histories: {e}")

        # O simulador entra SEMPRE, no fim — não como plano B.
        #
        # Era `real_history if real_history else [simulador]`: bastava existir uma
        # conversa no banco para a conversa que o operador acabou de conduzir ser
        # DESCARTADA INTEIRA. E é justamente ela o sinal mais deliberado que existe:
        # ele testou aquilo de propósito, para expor um comportamento. O operador
        # clicava em Diagnosticar logo depois de reproduzir o problema e recebia um
        # diagnóstico sobre outras conversas — parecia que a Mestre "testava sozinha".
        #
        # O comentário acima do bloco já dizia "combina" e "complementa"; o código
        # substituía. Agora combina de verdade, com o teste POR ÚLTIMO: é o mais
        # recente e é o que o operador quer discutir.
        simulado = [
            {"role": ("human" if (h.get("from") == "me" or h.get("role") == "user") else "ai"),
             "content": h.get("text") or h.get("content") or ""}
            for h in test_history
            if (h.get("text") or h.get("content"))
        ]
        history = real_history + simulado
        logger.info(
            f"[MESTRE] improve-prompt {location_id}/{channel} mode={mode}: "
            f"{len(real_history)} msgs reais + {len(simulado)} do simulador"
        )

        from utils.master_prompt import build_improve_messages
        messages = build_improve_messages(
            form_data=agent.form_data or {},
            current_prompt=agent.prompt or "",
            conversation_history=history,
            mode=mode,
            user_feedback=feedback,
        )

        api_key = settings_row.admin_openrouter_key
        model = settings_row.admin_openrouter_model or "openai/gpt-4o"

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "HTTP-Referer": "https://millochat.com",
                    "X-Title": "MilloChat Prompt Optimizer",
                },
                json={
                    "model": model,
                    "max_tokens": 6000,
                    "messages": messages,
                },
            )
            if resp.status_code != 200:
                logger.error(f"Erro OpenRouter improve-prompt: {resp.status_code} {resp.text}")
                return {"success": False, "error": "Erro ao chamar IA Mestre."}
            output = resp.json()["choices"][0]["message"]["content"]

        if mode == "apply":
            agent.prompt = output
            db.commit()
            try:
                from services.prompt_history import snapshot_prompt
                snapshot_prompt(
                    location_id=agent.location_id,
                    channel=agent.channel,
                    prompt=output,
                    source="optimize_apply",
                    agent_id=agent.id,
                    form_data_snapshot=agent.form_data,
                    note=feedback or None,
                )
            except Exception as e_snap:
                logger.warning(f"Falha snapshot prompt (optimize): {e_snap}")
            return {
                "success": True,
                "mode": "apply",
                "prompt": output,
                "history_used": len(history),
                "history_source": "real" if real_history else ("test" if history else "none"),
            }

        return {
            "success": True,
            "mode": "diagnose",
            "diagnosis": output,
            "history_used": len(history),
            "history_source": "real" if real_history else ("test" if history else "none"),
        }
    except Exception as e:
        db.rollback()
        logger.error(f"Erro em improve-prompt: {e}", exc_info=True)
        return {"success": False, "error": str(e)}
    finally:
        db.close()


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
            # Cria o agente do canal sob demanda (caso o usuário tenha
            # adicionado o canal mas ainda não salvou via aba Config).
            # Herda chaves de API do agente WhatsApp se existir (evita re-cadastro).
            agent_name = form_data.get("agent_name") or "Agente Inteligente"
            wa_agent = db.query(AIAgent).filter(
                AIAgent.location_id == location_id,
                AIAgent.channel == "whatsapp",
            ).first()
            agent = AIAgent(
                location_id=location_id,
                channel=channel,
                name=agent_name,
                prompt="Você é um assistente virtual prestativo.",
                api_key=(wa_agent.api_key if wa_agent else None),
                model=(wa_agent.model if wa_agent else "openai/gpt-4o"),
                groq_api_key=(wa_agent.groq_api_key if wa_agent else None),
                tts_provider=(wa_agent.tts_provider if wa_agent else "elevenlabs"),
                elevenlabs_api_key=(wa_agent.elevenlabs_api_key if wa_agent else None),
                elevenlabs_voice_id=(wa_agent.elevenlabs_voice_id if wa_agent else None),
                fishaudio_api_key=(wa_agent.fishaudio_api_key if wa_agent else None),
                fishaudio_voice_id=(wa_agent.fishaudio_voice_id if wa_agent else None),
                fishaudio_model=(wa_agent.fishaudio_model if wa_agent else "s1"),
                debounce_seconds=(wa_agent.debounce_seconds if wa_agent else 1.5),
            )
            db.add(agent)
            db.flush()
            logger.info(f"Agente criado sob demanda em form-data: location={location_id} channel={channel} (chaves herdadas do whatsapp={bool(wa_agent)})")

        agent.form_data = form_data
        if agent.name and form_data.get("agent_name"):
            agent.name = form_data["agent_name"]

        fields_override = None
        if regenerate:
            settings = db.query(SystemSettings).first()
            try:
                # Mesmo gate do onboarding: Anthropic-Spec quando há chave, senão
                # OpenRouter legado. No caminho Spec, os campos vêm da Mestre.
                generated_prompt, fields_override = await _run_master(settings, form_data, location_id)
            except _MasterError as e:
                db.commit()
                logger.error(f"IA Mestre falhou ao regenerar ({location_id}): {e}")
                return {"success": False, "error": str(e)}
            agent.prompt = generated_prompt

        # Esta é a ÚNICA tela onde `qualification_questions` de fato é digitado
        # (a aba Cadastro). Sem provisionar aqui, o operador escreve as perguntas,
        # o prompt muda, mas os campos nunca passam a existir — e o agente segue
        # sem a tool de qualificação.
        provisionamento = None
        if not agent.qualification_fields:
            from services.agent_provisioning import build_agent_provisioning

            provisionamento = await build_agent_provisioning(location_id, form_data, fields_override=fields_override)
            for chave, valor in provisionamento["config"].items():
                if valor not in (None, [], False) and not getattr(agent, chave, None):
                    setattr(agent, chave, valor)

        db.commit()

        if regenerate:
            try:
                from services.prompt_history import snapshot_prompt
                snapshot_prompt(
                    location_id=agent.location_id,
                    channel=agent.channel,
                    prompt=agent.prompt,
                    source="regenerate",
                    agent_id=agent.id,
                    form_data_snapshot=agent.form_data,
                )
            except Exception as e_snap:
                logger.warning(f"Falha snapshot prompt (regenerate): {e_snap}")

        return {
            "success": True,
            "prompt": agent.prompt if regenerate else None,
            "provisioning": provisionamento["report"] if provisionamento else None,
        }
    except Exception as e:
        logger.error(f"Erro ao salvar form_data: {e}", exc_info=True)
        db.rollback()
        return {"success": False, "error": str(e)}
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────
# Onboarding submissions (formulário público) → criar agente sob demanda
# ─────────────────────────────────────────────────────────────────────

@router.get("/onboarding/submissions")
async def list_onboarding_submissions(request: Request, location_id: Optional[str] = None):
    """Lista submissões do formulário público (filtra por tenant se passado)."""
    from admin.dashboard import verify_admin
    if not verify_admin(request.cookies.get("admin_session")):
        return {"success": False, "error": "Não autenticado."}

    db = SessionLocal()
    try:
        q = db.query(OnboardingSubmission)
        if location_id:
            q = q.filter(OnboardingSubmission.tenant_location_id == location_id)
        rows = q.order_by(OnboardingSubmission.created_at.desc()).limit(100).all()
        return {
            "success": True,
            "submissions": [
                {
                    "id": r.id,
                    "tenant_location_id": r.tenant_location_id,
                    "status": r.status,
                    "form_data": r.form_data,
                    "created_at": str(r.created_at) if r.created_at else None,
                    "processed_at": str(r.processed_at) if r.processed_at else None,
                }
                for r in rows
            ],
        }
    finally:
        db.close()


class _MasterError(Exception):
    """Falha da IA Mestre — o chamador mantém a submissão pending, sem criar meia-boca."""


async def _run_master(
    settings, form_data: dict, location_id: Optional[str] = None
) -> tuple[str, Optional[list]]:
    """
    Roda a IA Mestre e devolve (system_prompt, fields_override).

    Gate: se há chave Anthropic, usa o caminho ESTRUTURADO (AgentSpec) — a Mestre
    emite prompt + campos de qualificação como intenção. Senão, cai no legado
    OpenRouter (prosa), e os campos vêm do parser do texto livre (fields=None).
    Assim a migração é opt-in por configuração e reversível — sem regredir os
    tenants atuais até o admin configurar a chave.

    `location_id` é só para o gasto da Mestre cair no painel do tenant certo
    (aba Métricas, linha "Mestre"). Não muda o que é gerado.
    """
    from services import master_engine

    if master_engine.is_configured():
        try:
            spec = await master_engine.generate_agent_spec(form_data, location_id=location_id)
        except Exception as e:
            raise _MasterError(f"IA Mestre (Anthropic) falhou: {e}")
        from utils.agent_spec import spec_fields_as_intent
        fields = spec_fields_as_intent(spec) if spec.wants_qualification else []
        # A intenção do Spec vai para form_data também, para o prompt/def refletirem.
        if spec.agent_name:
            form_data["agent_name"] = spec.agent_name
        if spec.qualification_summary_prompt:
            form_data["_spec_summary_prompt"] = spec.qualification_summary_prompt
        return spec.system_prompt, fields

    # Legado OpenRouter — prosa; campos derivados do texto livre (fields=None).
    if not settings or not settings.admin_openrouter_key:
        raise _MasterError("IA Mestre não configurada (System Settings).")
    from utils.master_prompt import build_messages
    model = settings.admin_openrouter_model or "openai/gpt-4o"
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=_openrouter_headers(settings.admin_openrouter_key, "MilloChat Prompt Generator"),
            json={"model": model, "max_tokens": 6000, "messages": build_messages(form_data)},
        )
    if resp.status_code != 200:
        logger.error(f"Erro OpenRouter na IA Mestre: {resp.status_code} — {resp.text}")
        raise _MasterError("Erro ao gerar prompt com IA Mestre (OpenRouter).")
    return resp.json()["choices"][0]["message"]["content"], None


@router.post("/onboarding/submissions/{submission_id}/create-agent")
async def create_agent_from_submission(submission_id: int, request: Request):
    """
    Cria (ou atualiza) o agente WhatsApp do tenant a partir de uma submissão
    de onboarding, rodando a IA Mestre para gerar o prompt. Marca a submissão
    como 'processed'. Se a IA Mestre falhar, a submissão continua 'pending' e
    o dado permanece intacto.
    """
    from admin.dashboard import verify_admin
    if not verify_admin(request.cookies.get("admin_session")):
        return {"success": False, "error": "Não autenticado."}

    db = SessionLocal()
    try:
        submission = (
            db.query(OnboardingSubmission)
            .filter(OnboardingSubmission.id == submission_id)
            .first()
        )
        if not submission:
            return {"success": False, "error": "Submissão não encontrada."}

        location_id = submission.tenant_location_id
        tenant = db.query(Tenant).filter(Tenant.location_id == location_id).first()
        if not tenant:
            return {"success": False, "error": "Tenant não encontrado."}

        settings = db.query(SystemSettings).first()

        # Merge: respostas do cliente + defaults operacionais que a IA Mestre espera.
        form_data = dict(submission.form_data or {})
        form_data.setdefault("agent_name", "")
        form_data.setdefault("agent_type", "inbound")
        form_data.setdefault("tone_register", None)
        form_data.setdefault("restrictions", "")
        form_data.setdefault("qualification_questions", "")

        try:
            generated_prompt, fields_override = await _run_master(settings, form_data, location_id)
        except _MasterError as e:
            # Submissão permanece 'pending' — dado preservado, não cria meia-boca.
            logger.error(f"IA Mestre falhou na submissão {submission_id}: {e}")
            return {"success": False, "error": str(e)}

        # Config além do prompt: sem isto o agente nasce com UMA tool
        # (escalate_to_human) e é incapaz de qualificar — o prompt seria bonito e
        # inútil. Fail-closed: o que não der para determinar com segurança fica
        # desligado e vira pendência explícita no report.
        from services.agent_provisioning import build_agent_provisioning
        provisionamento = await build_agent_provisioning(location_id, form_data, fields_override=fields_override)
        cfg = provisionamento["config"]
        # Resumo de qualificação é decisão segura da Mestre (não é ID de CRM).
        if form_data.get("_spec_summary_prompt") and cfg.get("qualification_enabled"):
            cfg["qualification_summary_prompt"] = form_data["_spec_summary_prompt"]

        agent = (
            db.query(AIAgent)
            .filter(AIAgent.location_id == location_id, AIAgent.channel == "whatsapp")
            .first()
        )
        aplicado = dict(cfg)
        if not agent:
            agent = AIAgent(
                location_id=location_id,
                channel="whatsapp",
                name=form_data.get("agent_name") or "Agente Inteligente",
                prompt=generated_prompt,
                form_data=form_data,
                **cfg,
            )
            db.add(agent)
        else:
            agent.prompt = generated_prompt
            agent.form_data = form_data
            if form_data.get("agent_name"):
                agent.name = form_data["agent_name"]
            # Agente existente pode ter sido afinado à mão — regerar o prompt não
            # pode desfazer isso. Só preenchemos o que ainda está vazio, e o que
            # NÃO for aplicado sai do `aplicado` para o report não mentir.
            if agent.qualification_fields:
                # Campos curados pelo operador mandam: sem eles, pipeline/stage
                # e o enabled derivado também não se aplicam.
                aplicado = {}
            else:
                for chave, valor in list(aplicado.items()):
                    if valor in (None, [], False) or getattr(agent, chave, None):
                        aplicado.pop(chave, None)
                        continue
                    setattr(agent, chave, valor)
                # Ligar a qualificação só faz sentido junto do funil — e nunca
                # reverter um desligamento deliberado do operador.
                if "qualification_pipeline_id" not in aplicado:
                    aplicado.pop("qualification_enabled", None)

        submission.status = "processed"
        submission.processed_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(agent)

        try:
            from services.prompt_history import snapshot_prompt
            snapshot_prompt(
                location_id=location_id,
                channel=agent.channel or "whatsapp",
                prompt=agent.prompt,
                source="form",
                agent_id=agent.id,
                form_data_snapshot=form_data,
            )
        except Exception as e_snap:
            logger.warning(f"Falha snapshot prompt (create-from-onboarding): {e_snap}")

        # O report descreve o que FOI GRAVADO, não o que foi calculado — senão
        # avisaria "qualificação ligada" num agente cuja config manual venceu.
        report = dict(provisionamento["report"])
        if not aplicado:
            report = {
                **report,
                "qualification_enabled": bool(agent.qualification_enabled),
                "pipeline_definido": bool(agent.qualification_pipeline_id and agent.qualification_stage_id),
                "pendencias": (report.get("pendencias") or [])
                + ["Config de qualificação já existente foi PRESERVADA — nada sobrescrito."],
            }
        logger.info(
            f"Agente gerado da submissão {submission_id} para tenant {location_id} "
            f"({tenant.company_name}) — qualificação={'ligada' if report['qualification_enabled'] else 'desligada'}, "
            f"campos={len(report.get('fields') or [])}, pendências={len(report.get('pendencias') or [])}"
        )
        return {
            "success": True,
            "agent_id": agent.id,
            "location_id": location_id,
            # O operador precisa saber o que foi ligado sozinho e o que falta —
            # geração auditável, não mágica.
            "provisioning": report,
        }

    except Exception as e:
        logger.error(f"Erro ao criar agente da submissão {submission_id}: {e}", exc_info=True)
        db.rollback()
        return {"success": False, "error": str(e)}
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────
# Histórico de versões do prompt
# ─────────────────────────────────────────────────────────────────────

@router.get("/{location_id}/prompt-history")
async def list_prompt_history(
    location_id: str,
    channel: str = "whatsapp",
    limit: int = 30,
):
    """Lista as versões anteriores do prompt do agente (mais recentes primeiro)."""
    from services.prompt_history import list_history
    items = list_history(location_id, channel, limit=limit)
    return {"success": True, "count": len(items), "history": items}


@router.get("/prompt-history/{history_id}")
async def get_prompt_version(history_id: int):
    """Retorna o prompt completo de uma versão específica."""
    from services.prompt_history import get_version
    version = get_version(history_id)
    if not version:
        return {"success": False, "error": "Versão não encontrada."}
    return {"success": True, "version": version}


@router.post("/prompt-history/{history_id}/restore")
async def restore_prompt_version(history_id: int):
    """Restaura uma versão antiga como prompt vivo do agente."""
    from services.prompt_history import restore_version
    result = restore_version(history_id)
    if not result:
        return {"success": False, "error": "Não foi possível restaurar."}
    return result


# --------------------------------------------------------------------------- #
# Wizard de criação — o botão "+"
#
# Este router já nasce com `require_admin`, então as rotas abaixo são de operador
# por construção. O escopo por tenant é reforçado no service: `location_id` vem
# do path e é comparado com o dono do rascunho, para id sequencial não abrir
# porta de outra instância.
# --------------------------------------------------------------------------- #
@router.post("/{location_id}/wizard/abrir")
async def wizard_abrir(location_id: str, request: Request):
    """
    Abre (ou retoma) o rascunho DESTE operador nesta instância.

    O dono vem do cookie, não do corpo: quem abre é quem está logado.
    """
    from admin.dashboard import current_admin
    from services.draft_service import abrir

    if not is_valid_location_id(location_id):
        return {"success": False, "error": "location_id inválido."}
    operador = current_admin(request.cookies.get("admin_session"))
    try:
        return {"success": True, **await abrir(location_id, operador)}
    except Exception as e:
        logger.error(f"[WIZARD] Falha ao abrir rascunho de {location_id}: {type(e).__name__}")
        return {"success": False, "error": "Não foi possível abrir o assistente."}


@router.get("/{location_id}/wizard/{draft_id}")
async def wizard_estado(location_id: str, draft_id: int):
    from services.draft_service import RascunhoInvalido, estado

    if not is_valid_location_id(location_id):
        return {"success": False, "error": "location_id inválido."}
    try:
        return {"success": True, **await estado(draft_id, location_id)}
    except RascunhoInvalido as e:
        return {"success": False, "error": str(e)}


@router.post("/{location_id}/wizard/{draft_id}/etapa")
async def wizard_salvar_etapa(location_id: str, draft_id: int, request: Request):
    """Salva o que a pessoa preencheu. Incremental: sair e voltar não perde nada."""
    from services.draft_service import RascunhoInvalido, salvar

    if not is_valid_location_id(location_id):
        return {"success": False, "error": "location_id inválido."}
    try:
        mudancas = await request.json()
    except Exception:
        mudancas = {}
    try:
        return {"success": True, **await salvar(draft_id, location_id, mudancas or {})}
    except RascunhoInvalido as e:
        return {"success": False, "error": str(e)}


@router.post("/{location_id}/wizard/{draft_id}/importar")
async def wizard_importar(location_id: str, draft_id: int):
    """
    Traz para o rascunho o que o cliente respondeu na entrevista ou no formulário.

    Esta rota é o retorno que faltava. As portas "Conversar com a Mestre" e
    "Preencher formulário" abriam uma aba e gravavam só `origem`: a entrevista
    concluía, criava a `OnboardingSubmission`, e o rascunho continuava com
    `prompt=None`. `pode_publicar` reprovava para sempre — a porta marcada como
    RECOMENDADA não tinha como terminar. As colunas `submission_id`/`spec` já
    existiam e já eram lidas no publish: consumidor sem produtor.

    Roda a mesma IA Mestre do caminho da submissão (`_run_master`) — um gerador só,
    como manda `decisoes/entrevista-da-mestre`. A submissão só é marcada como
    processada no PUBLISH, não aqui: importar e desistir não pode consumir o
    trabalho que o cliente teve de responder.
    """
    from services.draft_service import (
        RascunhoInvalido, estado, salvar, submissao_pendente,
    )

    if not is_valid_location_id(location_id):
        return {"success": False, "error": "location_id inválido."}

    # O rascunho é validado ANTES de qualquer chamada paga. Rodar a Mestre e só
    # depois descobrir que o rascunho é de outro tenant (ou já foi publicado)
    # gastava uma chamada de LLM para devolver "não encontrado" — num id que é
    # sequencial e adivinhável.
    try:
        await estado(draft_id, location_id)
    except RascunhoInvalido as e:
        return {"success": False, "error": str(e)}

    sub = await submissao_pendente(location_id)
    if not sub:
        return {
            "success": False,
            "error": "Nada para trazer ainda — conclua a entrevista ou o formulário do cliente.",
        }
    submission_id, form_data = sub["id"], sub["form_data"]

    db = SessionLocal()
    try:
        settings = db.query(SystemSettings).first()
    finally:
        db.close()

    form_data.setdefault("agent_name", "")
    form_data.setdefault("agent_type", "inbound")
    form_data.setdefault("tone_register", None)
    form_data.setdefault("restrictions", "")
    form_data.setdefault("qualification_questions", "")

    try:
        prompt_gerado, campos = await _run_master(settings, form_data, location_id)
    except _MasterError as e:
        # A submissão continua 'pending' — o dado do cliente não se perde porque a
        # Mestre falhou.
        logger.error(f"[WIZARD] IA Mestre falhou ao importar para o rascunho {draft_id}: {e}")
        return {"success": False, "error": str(e)}

    mudancas = {
        "prompt": prompt_gerado,
        "spec": form_data,
        "submission_id": submission_id,
    }
    if form_data.get("agent_name"):
        mudancas["agent_name"] = form_data["agent_name"]

    # A Mestre disse QUAIS dados coletar. Sem isto, `build_agent_provisioning`
    # recebia spec vazio e devolvia `qualification_enabled: False` — o checkbox
    # "Qualificar leads" era uma promessa que nunca se cumpria.
    #
    # As duas chaves entram SEMPRE, inclusive vazias. Escrevê-las só quando havia
    # campos fazia a importação ligar e nunca desligar: reimportar depois de uma
    # entrevista que não pede qualificação deixava no rascunho os campos da
    # importação ANTERIOR, de outro negócio.
    mudancas["qualification_fields"] = list(campos or [])
    mudancas["qualificar"] = bool(campos)

    try:
        await salvar(draft_id, location_id, mudancas)
        atual = await estado(draft_id, location_id)
    except RascunhoInvalido as e:
        return {"success": False, "error": str(e)}

    logger.info(
        f"[WIZARD] Rascunho {draft_id} importou a submissão {submission_id} "
        f"({len(prompt_gerado or '')} chars, {len(campos or [])} campos)."
    )
    return {
        "success": True,
        "empresa": form_data.get("company_name") or "",
        "campos_de_qualificacao": len(campos or []),
        **atual,
    }


@router.post("/{location_id}/wizard/{draft_id}/publicar")
async def wizard_publicar(location_id: str, draft_id: int):
    """
    Cria o agente de verdade. Até aqui ele não existia para o runtime.

    O guard é reavaliado no service — a tela pode estar desatualizada, e publicar
    agente sem prompt o põe atendendo lead com nada a dizer.
    """
    from services.draft_service import RascunhoInvalido, publicar

    if not is_valid_location_id(location_id):
        return {"success": False, "error": "location_id inválido."}
    try:
        return {"success": True, **await publicar(draft_id, location_id)}
    except RascunhoInvalido as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.error(f"[WIZARD] Falha ao publicar {draft_id} de {location_id}: {type(e).__name__}")
        return {"success": False, "error": "Não foi possível publicar o agente."}
