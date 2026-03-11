import logging
from typing import Optional
from fastapi import APIRouter, Depends, Form, HTTPException, Request
import httpx
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from data.database import get_db, SessionLocal
from data.models import Tenant, AIAgent, SystemSettings
from auth.token_manager import token_manager
from datetime import datetime
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
    always_reply_with_audio: bool = Form(False),
    is_active: bool = Form(False)
):
    """
    Cria ou atualiza as configurações do Agente de IA para um Tenant específico.
    """
    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter(Tenant.location_id == location_id).first()
        if not tenant:
            logger.error(f"Tenant {location_id} não encontrado ao tentar salvar Agente IA.")
            return RedirectResponse(url="/admin/dashboard?err=Tenant+não+encontrado", status_code=303)

        # Busca agente existente ou cria novo
        agent = db.query(AIAgent).filter(AIAgent.location_id == location_id).first()

        if not agent:
            agent = AIAgent(location_id=location_id)
            db.add(agent)

        agent.name = name
        agent.prompt = prompt
        agent.model = model
        agent.api_key = api_key
        agent.elevenlabs_api_key = elevenlabs_api_key
        agent.elevenlabs_voice_id = elevenlabs_voice_id
        agent.always_reply_with_audio = always_reply_with_audio
        agent.is_active = is_active
        agent.updated_at = datetime.utcnow()

        db.commit()
        logger.info(f"Configurações do Agente IA atualizadas para o Tenant {location_id}.")

        return RedirectResponse(url="/admin/dashboard?msg=Agente+IA+atualizado+com+sucesso", status_code=303)

    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao salvar Agente IA para o tenant {location_id}: {e}", exc_info=True)
        return RedirectResponse(url="/admin/dashboard?err=Erro+ao+salvar+Agente+IA", status_code=303)
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


class AnalyzeRequest(BaseModel):
    prompt_text: str

@router.post("/analyze-prompt")
async def analyze_ai_prompt(payload: AnalyzeRequest):
    """
    Usa a API Key Global de Administrador (SystemSettings) para revisar
    e sugerir melhorias em um prompt do usuário.
    """
    db = SessionLocal()
    try:
        settings = db.query(SystemSettings).first()
        if not settings or not settings.admin_openrouter_key:
            return {"success": False, "error": "Chave API do Administrador não configurada. Configure no menu superior (Admin Settings)."}
            
        system_prompt = (
            "Você é um especialista em Prompt Engineering focado em Agentes de IA para WhatsApp (SDRs, Vendas B2B e Atendimento). "
            "Sua tarefa é analisar o prompt enviado, identificar problemas que fazem o agente 'falar demais' ou 'ignorar regras', "
            "e fornecer dicas diretas de melhoria. Seja extremamente objetivo, amigável e use Markdown estruturado (negrito, listas). "
            "Foque em regras de tamanho máximo, uso de perguntas singulares e redução de discursos extensos."
            "Termine sugerindo uma versão melhorada de regras de Limite de Resposta para colar no prompt."
        )
        
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.admin_openrouter_key}",
                    "HTTP-Referer": "https://axenwp.com",
                    "X-Title": "AxenWP Admin Prompt Analyzer",
                },
                json={
                    "model": settings.admin_openrouter_model or "openai/gpt-4o",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": f"Analise o seguinte prompt do meu agente e crie um feedback estruturado sobre o que está ruim e como melhorar:\n\n---\n{payload.prompt_text}"}
                    ]
                }
            )
            
            if resp.status_code != 200:
                logger.error(f"Erro OpenRouter Analyzer: {resp.text}")
                return {"success": False, "error": f"Erro na IA Mestre ({resp.status_code}). Verifique a chave do Admin."}
                
            data = resp.json()
            analysis = data["choices"][0]["message"]["content"]
            return {"success": True, "analysis": analysis}
            
    except Exception as e:
        logger.error(f"Erro no analisador de prompt: {e}", exc_info=True)
        return {"success": False, "error": "Erro interno do servidor ao tentar analisar."}
    finally:
        db.close()

