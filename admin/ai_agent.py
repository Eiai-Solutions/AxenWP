import logging
from typing import Optional
from fastapi import APIRouter, Depends, Form, HTTPException, Request
import httpx
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from data.database import get_db, SessionLocal
from data.models import Tenant, AIAgent
from auth.token_manager import token_manager

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
            raise HTTPException(status_code=404, detail="Tenant não encontrado.")

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
        
        db.commit()
        logger.info(f"Configurações do Agente IA atualizadas para o Tenant {location_id}.")
        
        return RedirectResponse(url="/admin/dashboard?msg=Agente+IA+atualizado+com+sucesso", status_code=303)
    
    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao salvar Agente IA para o tenant {location_id}: {e}")
        return RedirectResponse(url="/admin/dashboard?error=Erro+ao+salvar+Agente", status_code=303)
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
