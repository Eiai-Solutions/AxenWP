"""
Endpoints read-only para inspeção externa do banco.

Protegido por token estático (env INSPECT_TOKEN) via header X-Inspect-Token.
Pensado para uso em sessões automatizadas (assistentes IA, scripts) que
precisam analisar agentes, prompts e conversas sem cookie de admin nem
gravar nada.

Se INSPECT_TOKEN não estiver setado no .env, todos os endpoints retornam 503.

Mascaramento de sensíveis:
- API keys e tokens nunca aparecem completos (só prefix+suffix)
- Apenas leitura — zero rotas de mutação aqui
"""

from typing import Optional

from fastapi import APIRouter, Header, Path, Query, Request
from fastapi.responses import JSONResponse

from data.database import SessionLocal
from data.models import (
    AIAgent,
    AgentPromptHistory,
    ChatHistory,
    OnboardingSubmission,
    QualifiedLead,
    SystemSettings,
    Tenant,
    UsageLog,
)
from utils.config import settings as app_settings
from utils.limiter import limiter
from utils.logger import logger


router = APIRouter(prefix="/admin/inspect", tags=["Admin Inspect (read-only)"])


def _gate(token_header: Optional[str]):
    """Bloqueia se feature desligada ou token incorreto."""
    if not app_settings.inspect_token:
        return JSONResponse(
            {"success": False, "error": "INSPECT_TOKEN não configurado no servidor."},
            status_code=503,
        )
    if not token_header or token_header.strip() != app_settings.inspect_token.strip():
        return JSONResponse(
            {"success": False, "error": "Token de inspeção inválido."},
            status_code=401,
        )
    return None


def _mask(value: Optional[str], keep_prefix: int = 6, keep_suffix: int = 4) -> Optional[str]:
    """Mascara segredos: 'gsk_abc...xyz' em vez do valor inteiro."""
    if not value:
        return None
    if len(value) <= keep_prefix + keep_suffix + 3:
        return "***"
    return f"{value[:keep_prefix]}…{value[-keep_suffix:]}"


# ─────────────────────────────────────────────────────────────────────
# Health do módulo (verifica se token bate)
# ─────────────────────────────────────────────────────────────────────


@router.get("/ping")
@limiter.limit("60/minute")
async def inspect_ping(request: Request, x_inspect_token: Optional[str] = Header(None)):
    gated = _gate(x_inspect_token)
    if gated:
        return gated
    return {"success": True, "message": "Inspect token válido."}


# ─────────────────────────────────────────────────────────────────────
# Tenants
# ─────────────────────────────────────────────────────────────────────


@router.get("/tenants")
@limiter.limit("60/minute")
async def inspect_tenants(request: Request, x_inspect_token: Optional[str] = Header(None)):
    """Lista todos os tenants (resumo)."""
    gated = _gate(x_inspect_token)
    if gated:
        return gated

    db = SessionLocal()
    try:
        tenants = db.query(Tenant).all()
        return {
            "success": True,
            "count": len(tenants),
            "tenants": [
                {
                    "location_id": t.location_id,
                    "company_name": t.company_name,
                    "is_active": t.is_active,
                    "mode": t.mode,
                    "zapi_configured": bool(t.zapi_instance_id and t.zapi_token),
                    "telegram_configured": bool(t.telegram_bot_token),
                    "ghl_oauth_present": bool(t.access_token),
                    "ghl_pit_present": bool(t.pit_token),
                    "ghl_token_valid": not t.is_token_expired,
                    "created_at": t.created_at,
                }
                for t in tenants
            ],
        }
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────
# Agente
# ─────────────────────────────────────────────────────────────────────


@router.get("/agent/{location_id}")
@limiter.limit("60/minute")
async def inspect_agent(
    request: Request,
    location_id: str = Path(...),
    channel: str = Query("whatsapp"),
    x_inspect_token: Optional[str] = Header(None),
):
    """Configuração completa do agente (prompt incluído, segredos mascarados)."""
    gated = _gate(x_inspect_token)
    if gated:
        return gated

    db = SessionLocal()
    try:
        agent = (
            db.query(AIAgent)
            .filter(AIAgent.location_id == location_id, AIAgent.channel == channel)
            .first()
        )
        if not agent:
            return {"success": False, "error": "Agente não encontrado."}

        return {
            "success": True,
            "agent": {
                "id": agent.id,
                "location_id": agent.location_id,
                "channel": agent.channel,
                "linked_to_channel": agent.linked_to_channel,
                "name": agent.name,
                "model": agent.model,
                "is_active": agent.is_active,
                "debounce_seconds": float(agent.debounce_seconds or 0),
                "api_key_masked": _mask(agent.api_key),
                "tts_provider": agent.tts_provider or "elevenlabs",
                "elevenlabs_api_key_masked": _mask(agent.elevenlabs_api_key),
                "elevenlabs_voice_id": agent.elevenlabs_voice_id,
                "fishaudio_api_key_masked": _mask(agent.fishaudio_api_key),
                "fishaudio_voice_id": agent.fishaudio_voice_id,
                "fishaudio_model": agent.fishaudio_model or "s1",
                "fishaudio_speed": float(agent.fishaudio_speed) if agent.fishaudio_speed is not None else 1.0,
                "groq_api_key_masked": _mask(agent.groq_api_key),
                "qualification_enabled": bool(agent.qualification_enabled),
                "qualification_pipeline_id": agent.qualification_pipeline_id,
                "qualification_stage_id": agent.qualification_stage_id,
                "qualification_fields": agent.qualification_fields,
                "form_data": agent.form_data,
                "prompt": agent.prompt,
                "prompt_length": len(agent.prompt or ""),
                "created_at": str(agent.created_at) if agent.created_at else None,
                "updated_at": str(agent.updated_at) if agent.updated_at else None,
            },
        }
    finally:
        db.close()


@router.get("/agent/{location_id}/channels")
@limiter.limit("60/minute")
async def inspect_agent_channels(
    request: Request,
    location_id: str = Path(...),
    x_inspect_token: Optional[str] = Header(None),
):
    """Lista todos os canais (agentes) configurados para o tenant."""
    gated = _gate(x_inspect_token)
    if gated:
        return gated

    db = SessionLocal()
    try:
        agents = db.query(AIAgent).filter(AIAgent.location_id == location_id).all()
        return {
            "success": True,
            "count": len(agents),
            "agents": [
                {
                    "id": a.id,
                    "channel": a.channel,
                    "name": a.name,
                    "is_active": a.is_active,
                    "model": a.model,
                    "linked_to_channel": a.linked_to_channel,
                    "qualification_enabled": bool(a.qualification_enabled),
                    "prompt_length": len(a.prompt or ""),
                }
                for a in agents
            ],
        }
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────
# Histórico de prompts
# ─────────────────────────────────────────────────────────────────────


@router.get("/agent/{location_id}/history")
@limiter.limit("60/minute")
async def inspect_agent_history(
    request: Request,
    location_id: str = Path(...),
    channel: str = Query("whatsapp"),
    limit: int = Query(30, ge=1, le=100),
    x_inspect_token: Optional[str] = Header(None),
):
    """Lista versões do prompt do agente (mais recentes primeiro)."""
    gated = _gate(x_inspect_token)
    if gated:
        return gated

    db = SessionLocal()
    try:
        rows = (
            db.query(AgentPromptHistory)
            .filter(
                AgentPromptHistory.location_id == location_id,
                AgentPromptHistory.channel == channel,
            )
            .order_by(AgentPromptHistory.created_at.desc())
            .limit(limit)
            .all()
        )
        return {
            "success": True,
            "count": len(rows),
            "versions": [
                {
                    "id": r.id,
                    "source": r.source,
                    "note": r.note,
                    "prompt_length": len(r.prompt or ""),
                    "prompt_preview": (r.prompt or "")[:300],
                    "created_at": str(r.created_at) if r.created_at else None,
                }
                for r in rows
            ],
        }
    finally:
        db.close()


@router.get("/prompt-version/{version_id}")
@limiter.limit("60/minute")
async def inspect_prompt_version(
    request: Request,
    version_id: int = Path(...),
    x_inspect_token: Optional[str] = Header(None),
):
    """Retorna o prompt completo de uma versão histórica específica."""
    gated = _gate(x_inspect_token)
    if gated:
        return gated

    db = SessionLocal()
    try:
        r = (
            db.query(AgentPromptHistory)
            .filter(AgentPromptHistory.id == version_id)
            .first()
        )
        if not r:
            return {"success": False, "error": "Versão não encontrada."}
        return {
            "success": True,
            "version": {
                "id": r.id,
                "location_id": r.location_id,
                "channel": r.channel,
                "agent_id": r.agent_id,
                "source": r.source,
                "note": r.note,
                "prompt": r.prompt,
                "form_data_snapshot": r.form_data_snapshot,
                "created_at": str(r.created_at) if r.created_at else None,
            },
        }
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────
# Conversas
# ─────────────────────────────────────────────────────────────────────


@router.get("/agent/{location_id}/conversations")
@limiter.limit("60/minute")
async def inspect_conversations(
    request: Request,
    location_id: str = Path(...),
    phone: Optional[str] = Query(None, description="Filtra por número específico"),
    limit: int = Query(50, ge=1, le=200),
    x_inspect_token: Optional[str] = Header(None),
):
    """Últimas mensagens do histórico de chat de um tenant (ou de um phone específico)."""
    gated = _gate(x_inspect_token)
    if gated:
        return gated

    db = SessionLocal()
    try:
        q = db.query(ChatHistory).filter(ChatHistory.location_id == location_id)
        if phone:
            session_id = f"{location_id}_{phone}"
            q = q.filter(ChatHistory.session_id == session_id)
        rows = q.order_by(ChatHistory.id.desc()).limit(limit).all()
        rows.reverse()
        return {
            "success": True,
            "count": len(rows),
            "messages": [
                {
                    "id": r.id,
                    "session_id": r.session_id,
                    "type": r.message_type,
                    "content": r.content,
                    "created_at": str(r.created_at) if r.created_at else None,
                }
                for r in rows
            ],
        }
    finally:
        db.close()


@router.get("/agent/{location_id}/sessions")
@limiter.limit("60/minute")
async def inspect_sessions(
    request: Request,
    location_id: str = Path(...),
    x_inspect_token: Optional[str] = Header(None),
):
    """Lista session_ids únicos com contagem de mensagens (top 50 mais recentes)."""
    gated = _gate(x_inspect_token)
    if gated:
        return gated

    from sqlalchemy import func

    db = SessionLocal()
    try:
        rows = (
            db.query(
                ChatHistory.session_id,
                func.count(ChatHistory.id).label("msg_count"),
                func.max(ChatHistory.created_at).label("last_message_at"),
            )
            .filter(ChatHistory.location_id == location_id)
            .group_by(ChatHistory.session_id)
            .order_by(func.max(ChatHistory.created_at).desc())
            .limit(50)
            .all()
        )
        return {
            "success": True,
            "count": len(rows),
            "sessions": [
                {
                    "session_id": r.session_id,
                    "phone": r.session_id.split("_", 1)[1] if "_" in r.session_id else None,
                    "message_count": r.msg_count,
                    "last_message_at": str(r.last_message_at) if r.last_message_at else None,
                }
                for r in rows
            ],
        }
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────
# Leads qualificados
# ─────────────────────────────────────────────────────────────────────


@router.get("/qualified-leads")
@limiter.limit("60/minute")
async def inspect_qualified_leads(
    request: Request,
    location_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    x_inspect_token: Optional[str] = Header(None),
):
    """Lista leads qualificados (filtra por tenant se passado)."""
    gated = _gate(x_inspect_token)
    if gated:
        return gated

    db = SessionLocal()
    try:
        q = db.query(QualifiedLead)
        if location_id:
            q = q.filter(QualifiedLead.location_id == location_id)
        rows = q.order_by(QualifiedLead.created_at.desc()).limit(limit).all()
        return {
            "success": True,
            "count": len(rows),
            "leads": [
                {
                    "id": r.id,
                    "location_id": r.location_id,
                    "phone": r.phone,
                    "ghl_opportunity_id": r.ghl_opportunity_id,
                    "qualified_data": r.qualified_data,
                    "summary": r.summary,
                    "created_at": str(r.created_at) if r.created_at else None,
                }
                for r in rows
            ],
        }
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────
# Configurações globais (mascaradas)
# ─────────────────────────────────────────────────────────────────────


@router.get("/system")
@limiter.limit("60/minute")
async def inspect_system(request: Request, x_inspect_token: Optional[str] = Header(None)):
    """SystemSettings com chaves mascaradas."""
    gated = _gate(x_inspect_token)
    if gated:
        return gated

    db = SessionLocal()
    try:
        ss = db.query(SystemSettings).first()
        if not ss:
            return {"success": True, "system_settings": None}
        return {
            "success": True,
            "system_settings": {
                "admin_openrouter_key_masked": _mask(ss.admin_openrouter_key),
                "admin_openrouter_model": ss.admin_openrouter_model,
                "admin_groq_api_key_masked": _mask(ss.admin_groq_api_key),
                "updated_at": str(ss.updated_at) if ss.updated_at else None,
            },
        }
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────
# Métricas de uso
# ─────────────────────────────────────────────────────────────────────


@router.get("/webhooks/recent")
@limiter.limit("60/minute")
async def inspect_recent_webhooks(
    request: Request,
    x_inspect_token: Optional[str] = Header(None),
):
    """Últimos webhooks Z-API/Telegram recebidos (buffer in-memory)."""
    gated = _gate(x_inspect_token)
    if gated:
        return gated
    from webhooks.zapi_receiver import get_recent_webhooks
    items = get_recent_webhooks()
    return {"success": True, "count": len(items), "webhooks": items}


@router.get("/processings/recent")
@limiter.limit("60/minute")
async def inspect_recent_processings(
    request: Request,
    x_inspect_token: Optional[str] = Header(None),
):
    """Últimos processamentos do AI engine (decisão TTS, status, etc)."""
    gated = _gate(x_inspect_token)
    if gated:
        return gated
    from services.ai_service import get_recent_processings
    items = get_recent_processings()
    return {"success": True, "count": len(items), "processings": items}


@router.get("/zapi/{location_id}/webhook-url")
@limiter.limit("30/minute")
async def inspect_zapi_webhook_url(
    request: Request,
    location_id: str = Path(...),
    x_inspect_token: Optional[str] = Header(None),
):
    """Lê a URL on-receive registrada na Z-API para este tenant — confirma se aponta pro servidor certo."""
    gated = _gate(x_inspect_token)
    if gated:
        return gated

    from services.zapi_service import zapi_service

    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter(Tenant.location_id == location_id).first()
        if not tenant:
            return {"success": False, "error": f"Tenant {location_id} não existe."}
        if not (tenant.zapi_instance_id and tenant.zapi_token):
            return {"success": False, "error": "Z-API não configurada para este tenant."}
        instance = tenant.zapi_instance_id
        zapi_token = tenant.zapi_token
        client_token = tenant.zapi_client_token or ""
    finally:
        db.close()

    expected = f"{(app_settings.public_base_url or '').strip().rstrip('/')}/webhook/zapi/inbound/{location_id}"
    current = await zapi_service.get_webhook_received(instance, zapi_token, client_token)
    current_url = (current or {}).get("value") or (current or {}).get("url") or None
    return {
        "success": True,
        "location_id": location_id,
        "expected_webhook_url": expected,
        "current_webhook_url": current_url,
        "matches": bool(current_url and current_url.rstrip("/") == expected.rstrip("/")),
        "raw_response": current,
    }


@router.get("/onboarding")
@limiter.limit("60/minute")
async def inspect_onboarding(
    request: Request,
    location_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    x_inspect_token: Optional[str] = Header(None),
):
    """Submissões do formulário público (filtra por tenant se passado)."""
    gated = _gate(x_inspect_token)
    if gated:
        return gated

    db = SessionLocal()
    try:
        q = db.query(OnboardingSubmission)
        if location_id:
            q = q.filter(OnboardingSubmission.tenant_location_id == location_id)
        rows = q.order_by(OnboardingSubmission.created_at.desc()).limit(limit).all()
        return {
            "success": True,
            "count": len(rows),
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


@router.get("/usage")
@limiter.limit("60/minute")
async def inspect_usage(
    request: Request,
    location_id: Optional[str] = Query(None),
    days: int = Query(7, ge=1, le=90),
    x_inspect_token: Optional[str] = Header(None),
):
    """Resumo de uso (OpenRouter/Groq/ElevenLabs) nos últimos N dias."""
    gated = _gate(x_inspect_token)
    if gated:
        return gated

    from datetime import datetime, timedelta, timezone
    from sqlalchemy import func

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    db = SessionLocal()
    try:
        q = db.query(
            UsageLog.location_id,
            UsageLog.service,
            func.count(UsageLog.id).label("calls"),
            func.sum(UsageLog.input_tokens).label("input_tokens"),
            func.sum(UsageLog.output_tokens).label("output_tokens"),
            func.sum(UsageLog.characters).label("characters"),
            func.sum(UsageLog.cost_usd).label("cost_usd"),
        ).filter(UsageLog.created_at >= cutoff)
        if location_id:
            q = q.filter(UsageLog.location_id == location_id)
        rows = q.group_by(UsageLog.location_id, UsageLog.service).all()

        return {
            "success": True,
            "period_days": days,
            "rows": [
                {
                    "location_id": r.location_id,
                    "service": r.service,
                    "calls": r.calls,
                    "input_tokens": int(r.input_tokens or 0),
                    "output_tokens": int(r.output_tokens or 0),
                    "characters": int(r.characters or 0),
                    "cost_usd": float(r.cost_usd or 0.0),
                }
                for r in rows
            ],
        }
    finally:
        db.close()
