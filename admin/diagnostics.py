"""
Endpoints de diagnóstico do sistema.

Separado de admin/seed_joorney.py para evitar que esse módulo continue crescendo
como pasta de despejo. Toda inspeção, validação de chaves e teste de pipeline
fica aqui.

Em produção, esses endpoints podem ser desabilitados via variável de ambiente
DEBUG_ENDPOINTS_ENABLED=false.
"""

import base64
import os
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import JSONResponse

from utils.logger import logger
from utils.config import settings as app_settings
from data.database import SessionLocal
from data.models import AIAgent, SystemSettings, Tenant
from auth.token_manager import token_manager
from admin.dashboard import verify_admin


router = APIRouter(prefix="/admin/diagnostics", tags=["Admin Diagnostics"])


def _diagnostics_enabled() -> bool:
    """Permite desligar todos os endpoints em produção via env var."""
    flag = os.getenv("DEBUG_ENDPOINTS_ENABLED", "true").lower()
    return flag not in ("false", "0", "no")


def _gate(authenticated: bool):
    """Bloqueia se não autenticado ou se o flag desligou os diagnósticos."""
    if not authenticated:
        return JSONResponse({"success": False, "error": "Não autenticado."}, status_code=401)
    if not _diagnostics_enabled():
        return JSONResponse({"success": False, "error": "Diagnostics desabilitado."}, status_code=404)
    return None


# ─────────────────────────────────────────────────────────────────────
# Health & Status
# ─────────────────────────────────────────────────────────────────────

@router.get("/health")
async def diagnostics_health(authenticated: bool = Depends(verify_admin)):
    """Status genérico do módulo de diagnóstico."""
    gated = _gate(authenticated)
    if gated:
        return gated
    return JSONResponse({
        "success": True,
        "diagnostics_enabled": _diagnostics_enabled(),
        "public_base_url_configured": bool(app_settings.public_base_url),
    })


@router.get("/tenant/{location_id}/status")
async def tenant_status(location_id: str, authenticated: bool = Depends(verify_admin)):
    """Diagnóstico de um tenant: agente, chaves, Z-API, GHL."""
    gated = _gate(authenticated)
    if gated:
        return gated

    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter(Tenant.location_id == location_id).first()
        if not tenant:
            return JSONResponse({"success": False, "error": f"Tenant {location_id} não existe."})

        agent = (
            db.query(AIAgent)
            .filter(AIAgent.location_id == location_id, AIAgent.channel == "whatsapp")
            .first()
        )
        ss = db.query(SystemSettings).first()
        groq_global = bool(ss and ss.admin_groq_api_key)

        return JSONResponse({
            "success": True,
            "tenant": {
                "company_name": tenant.company_name,
                "location_id": tenant.location_id,
                "is_active": tenant.is_active,
                "mode": tenant.mode,
                "zapi_configured": bool(tenant.zapi_instance_id and tenant.zapi_token),
                "telegram_configured": bool(tenant.telegram_bot_token),
                "ghl_oauth_present": bool(tenant.access_token),
                "ghl_pit_present": bool(tenant.pit_token),
            },
            "agent": {
                "exists": bool(agent),
                "name": agent.name if agent else None,
                "is_active": agent.is_active if agent else False,
                "has_openrouter_key": bool(agent and agent.api_key),
                "has_elevenlabs_key": bool(agent and agent.elevenlabs_api_key),
                "has_elevenlabs_voice": bool(agent and agent.elevenlabs_voice_id),
                "has_groq_per_agent": bool(agent and agent.groq_api_key),
                "model": agent.model if agent else None,
            },
            "global_groq_configured": groq_global,
            "global_groq_prefix": (ss.admin_groq_api_key[:8] + "…") if groq_global else None,
            "stt_will_work": bool((agent and agent.groq_api_key) or groq_global),
            "tts_will_work": bool(agent and agent.elevenlabs_api_key and agent.elevenlabs_voice_id),
        })
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────
# Buffers em memória (webhooks recebidos, processamentos do engine)
# ─────────────────────────────────────────────────────────────────────

@router.get("/webhooks")
async def recent_webhooks(authenticated: bool = Depends(verify_admin)):
    """Últimos payloads recebidos pelo webhook Z-API (resumo seguro)."""
    gated = _gate(authenticated)
    if gated:
        return gated
    from webhooks.zapi_receiver import get_recent_webhooks
    items = get_recent_webhooks()
    return JSONResponse({"success": True, "count": len(items), "webhooks": items})


@router.get("/processings")
async def recent_processings(authenticated: bool = Depends(verify_admin)):
    """Últimos processamentos do AI engine (decisão TTS, status, etc)."""
    gated = _gate(authenticated)
    if gated:
        return gated
    from services.ai_service import get_recent_processings
    items = get_recent_processings()
    return JSONResponse({"success": True, "count": len(items), "processings": items})


# ─────────────────────────────────────────────────────────────────────
# Groq (chave global de STT)
# ─────────────────────────────────────────────────────────────────────

@router.get("/groq/inspect")
async def groq_inspect(authenticated: bool = Depends(verify_admin)):
    """Mostra metadados (não a chave) salvos no banco para diagnosticar truncamento."""
    gated = _gate(authenticated)
    if gated:
        return gated
    db = SessionLocal()
    try:
        ss = db.query(SystemSettings).first()
        key = (ss.admin_groq_api_key if ss else None) or ""
    finally:
        db.close()
    return JSONResponse({
        "stored_in_db": bool(key),
        "length": len(key),
        "prefix": key[:8] if key else None,
        "suffix": key[-4:] if len(key) >= 4 else None,
        "has_leading_space": key != key.lstrip() if key else False,
        "has_trailing_space": key != key.rstrip() if key else False,
        "has_newline": "\n" in key or "\r" in key,
        "has_internal_space": " " in key.strip(),
        "starts_with_gsk_": key.startswith("gsk_") if key else False,
    })


@router.post("/groq/set")
async def groq_set(request: Request, authenticated: bool = Depends(verify_admin)):
    """Sobrescreve a chave Groq global aplicando strip agressivo. Body: {key}."""
    gated = _gate(authenticated)
    if gated:
        return gated

    body = await request.json()
    raw = body.get("key") or ""
    cleaned = raw.strip().replace("\r", "").replace("\n", "")
    if not cleaned.startswith("gsk_"):
        return JSONResponse({"success": False, "error": "Chave deve começar com gsk_."})

    db = SessionLocal()
    try:
        ss = db.query(SystemSettings).first()
        if not ss:
            ss = SystemSettings()
            db.add(ss)
        ss.admin_groq_api_key = cleaned
        db.commit()
        return JSONResponse({
            "success": True,
            "stored_length": len(cleaned),
            "stored_prefix": cleaned[:8],
            "stored_suffix": cleaned[-4:],
        })
    except Exception as e:
        db.rollback()
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)
    finally:
        db.close()


@router.get("/groq/test")
async def groq_test(authenticated: bool = Depends(verify_admin)):
    """Testa a chave Groq global enviando um POST direto à API /models."""
    gated = _gate(authenticated)
    if gated:
        return gated

    db = SessionLocal()
    try:
        ss = db.query(SystemSettings).first()
        groq_key = ss.admin_groq_api_key if (ss and ss.admin_groq_api_key) else None
    finally:
        db.close()

    if not groq_key:
        return JSONResponse({"success": False, "error": "Sem chave Groq global configurada."})

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {groq_key}"},
            )
            ok = resp.status_code == 200
            return JSONResponse({
                "success": ok,
                "status_code": resp.status_code,
                "groq_key_prefix": groq_key[:8] + "…",
                "body_preview": (resp.json() if ok else resp.text[:500]),
            })
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/groq/clear-agent-keys")
@router.get("/groq/clear-agent-keys")
async def groq_clear_agent_keys(authenticated: bool = Depends(verify_admin)):
    """Zera groq_api_key de todos os agentes (passam a usar a chave global)."""
    gated = _gate(authenticated)
    if gated:
        return gated

    db = SessionLocal()
    try:
        agents = db.query(AIAgent).filter(AIAgent.groq_api_key.isnot(None)).all()
        cleared = [
            {"location_id": a.location_id, "channel": a.channel, "name": a.name}
            for a in agents
        ]
        for a in agents:
            a.groq_api_key = None
        db.commit()
        return JSONResponse({
            "success": True,
            "cleared_count": len(cleared),
            "cleared_agents": cleared,
        })
    except Exception as e:
        db.rollback()
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────
# Pipeline de áudio ponta a ponta (per tenant)
# ─────────────────────────────────────────────────────────────────────

@router.get("/audio-pipeline/{location_id}")
async def audio_pipeline(location_id: str, authenticated: bool = Depends(verify_admin)):
    """
    Diagnóstico completo do pipeline de áudio ponta a ponta para um tenant.

    1. Confere chaves (Groq global + ElevenLabs do agente)
    2. Pega o último webhook de áudio que chegou
    3. Baixa o áudio referenciado
    4. Envia ao Groq Whisper e retorna a transcrição
    """
    gated = _gate(authenticated)
    if gated:
        return gated

    from webhooks.zapi_receiver import get_recent_webhooks

    result = {
        "step_1_keys": None,
        "step_2_last_webhook": None,
        "step_3_download_audio": None,
        "step_4_groq_transcribe": None,
        "verdict": None,
    }

    db = SessionLocal()
    try:
        ss = db.query(SystemSettings).first()
        groq_key = ss.admin_groq_api_key if (ss and ss.admin_groq_api_key) else None
        agent = (
            db.query(AIAgent)
            .filter(AIAgent.location_id == location_id, AIAgent.channel == "whatsapp")
            .first()
        )
    finally:
        db.close()

    result["step_1_keys"] = {
        "groq_global_set": bool(groq_key),
        "groq_global_prefix": (groq_key[:8] + "…") if groq_key else None,
        "groq_per_agent_set": bool(agent and agent.groq_api_key),
        "agent_active": bool(agent and agent.is_active),
        "elevenlabs_api_key_set": bool(agent and agent.elevenlabs_api_key),
        "elevenlabs_voice_set": bool(agent and agent.elevenlabs_voice_id),
        "tts_will_work": bool(agent and agent.elevenlabs_api_key and agent.elevenlabs_voice_id),
    }

    if not groq_key and not (agent and agent.groq_api_key):
        result["verdict"] = "BREAK at step 1: nenhuma chave Groq disponível."
        return JSONResponse(result)

    effective_groq = (agent.groq_api_key if (agent and agent.groq_api_key) else groq_key)

    webhooks = get_recent_webhooks()
    if not webhooks:
        result["step_2_last_webhook"] = {
            "received": False,
            "note": "nenhum webhook capturado desde o último deploy",
        }
        result["verdict"] = "BREAK at step 2: webhook do Z-API não chegou no servidor desde o último restart."
        return JSONResponse(result)

    # Filtra só webhooks com áudio do tenant em questão
    audio_webhooks = [
        w for w in webhooks
        if (w.get("audio_keys") or w.get("voice_keys"))
        and w.get("location_id") == location_id
    ]
    if not audio_webhooks:
        result["step_2_last_webhook"] = {
            "received": True,
            "total_webhooks": len(webhooks),
            "any_audio_for_this_tenant": False,
            "last_webhook_summary": webhooks[-1],
        }
        result["verdict"] = "BREAK at step 2: webhooks chegando mas nenhum era áudio deste tenant."
        return JSONResponse(result)

    last_audio = audio_webhooks[-1]
    audio_url = last_audio.get("audio_url_audioUrl") or last_audio.get("audio_url_url")
    result["step_2_last_webhook"] = {
        "received": True,
        "audio_keys": last_audio.get("audio_keys"),
        "voice_keys": last_audio.get("voice_keys"),
        "extracted_url": audio_url,
        "received_at": last_audio.get("received_at"),
    }
    if not audio_url:
        result["verdict"] = "BREAK at step 2: áudio chegou mas URL não foi extraída do payload."
        return JSONResponse(result)

    audio_bytes = None
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            dl = await client.get(audio_url)
            result["step_3_download_audio"] = {
                "status_code": dl.status_code,
                "content_length": len(dl.content) if dl.status_code == 200 else None,
                "content_type": dl.headers.get("content-type"),
            }
            if dl.status_code == 200:
                audio_bytes = dl.content
            else:
                result["verdict"] = f"BREAK at step 3: falha ao baixar áudio ({dl.status_code})."
                return JSONResponse(result)
    except Exception as e:
        result["step_3_download_audio"] = {"error": str(e)}
        result["verdict"] = f"BREAK at step 3: exceção ao baixar áudio: {e}"
        return JSONResponse(result)

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {effective_groq}"},
                data={"model": "whisper-large-v3", "language": "pt", "response_format": "text"},
                files={"file": ("audio.ogg", audio_bytes, "audio/ogg")},
            )
            transcription = resp.text.strip() if resp.status_code == 200 else None
            result["step_4_groq_transcribe"] = {
                "status_code": resp.status_code,
                "transcription": transcription,
                "body_preview_on_error": resp.text[:500] if resp.status_code != 200 else None,
            }
            if resp.status_code == 200 and transcription:
                result["verdict"] = "PIPELINE COMPLETO COM SUCESSO."
            elif resp.status_code != 200:
                result["verdict"] = f"BREAK at step 4: Groq retornou {resp.status_code}."
            else:
                result["verdict"] = "BREAK at step 4: transcrição vazia."
    except Exception as e:
        result["step_4_groq_transcribe"] = {"error": str(e)}
        result["verdict"] = f"BREAK at step 4: exceção: {e}"

    return JSONResponse(result)


# ─────────────────────────────────────────────────────────────────────
# Z-API webhook (re-registrar URL de inbound)
# ─────────────────────────────────────────────────────────────────────

@router.post("/zapi/{location_id}/sync-webhook")
@router.get("/zapi/{location_id}/sync-webhook")
async def zapi_sync_webhook(location_id: str, request: Request, authenticated: bool = Depends(verify_admin)):
    """Re-registra a URL de webhook 'on-receive' da Z-API apontando para este servidor."""
    gated = _gate(authenticated)
    if gated:
        return gated

    from services.zapi_service import zapi_service

    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter(Tenant.location_id == location_id).first()
        if not tenant:
            return JSONResponse({"success": False, "error": f"Tenant {location_id} não existe."})
        if not (tenant.zapi_instance_id and tenant.zapi_token):
            return JSONResponse({"success": False, "error": "Z-API não configurada."})
    finally:
        db.close()

    public_base = (app_settings.public_base_url or "").strip().rstrip("/")
    if not public_base:
        return JSONResponse({"success": False, "error": "PUBLIC_BASE_URL não está no .env."})

    target_url = f"{public_base}/webhook/zapi/inbound/{location_id}"

    current = await zapi_service.get_webhook_received(
        tenant.zapi_instance_id, tenant.zapi_token, tenant.zapi_client_token or ""
    )
    current_url = (current or {}).get("value") or (current or {}).get("url")

    ok = await zapi_service.set_webhook_received(
        tenant.zapi_instance_id, tenant.zapi_token, target_url, tenant.zapi_client_token or ""
    )
    return JSONResponse({
        "success": ok,
        "tenant": tenant.company_name,
        "location_id": location_id,
        "previous_webhook_url": current_url,
        "new_webhook_url": target_url,
    })
