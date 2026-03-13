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
            
        # 1. Simulate a synthetic conversation
        simulation_prompt = (
            "Você é um 'Lead' (cliente em potencial) interessado nos serviços da empresa, mas você é ocupado, direto e um pouco cético. "
            "Você deve simular uma conversa de 3 turnos com o Agente de IA. "
            "Abaixo estão as instruções do Agente (Prompt):\n"
            f"--- PROMPT DO AGENTE ---\n{payload.prompt_text}\n\n"
            "Retorne a transcrição da conversa no seguinte formato:\n"
            "Lead: [sua pergunta]\n"
            "Agente: [resposta baseada no prompt]\n"
            "...\n\n"
            "Gere uma conversa curta de 3 turnos."
        )

        async with httpx.AsyncClient(timeout=60.0) as client:
            # Simulação
            sim_resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.admin_openrouter_key}",
                    "HTTP-Referer": "https://axenwp.com",
                    "X-Title": "AxenWP Admin Simulation",
                },
                json={
                    "model": settings.admin_openrouter_model or "openai/gpt-4o",
                    "messages": [{"role": "user", "content": simulation_prompt}]
                }
            )
            transcript = sim_resp.json()["choices"][0]["message"]["content"] if sim_resp.status_code == 200 else "Falha na simulação."

            # 2. Final Analysis with Transcript
            # Usamos delimitadores XML em vez de JSON — muito mais robusto para
            # prompts longos com markdown, aspas e quebras de linha que corrompem JSON.
            system_prompt = (
                "Você é um especialista em Prompt Engineering para WhatsApp B2B.\n\n"
                "Sua tarefa é MELHORAR o prompt de um agente, não reescrevê-lo do zero.\n\n"
                "REGRAS ABSOLUTAS para o improved_prompt:\n"
                "1. PRESERVE todas as seções existentes (identidade, regras, tools, políticas, filtros, etc.).\n"
                "2. PRESERVE todas as instruções de tools, integrações e comportamentos especiais.\n"
                "3. Apenas melhore: tom das frases, clareza das instruções, ordem lógica das seções e concisão das explicações.\n"
                "4. NÃO remova seções, NÃO simplifique regras funcionais, NÃO descarte conteúdo.\n"
                "5. O improved_prompt deve ter tamanho similar ao original — nunca drasticamente menor.\n"
                "6. Jamais use placeholders como '[...]', '[mantido]', '[resto do prompt]' ou similares.\n\n"
                "Retorne a resposta EXATAMENTE neste formato:\n\n"
                "<analysis>\n"
                "[Feedback em markdown: o que estava bom, o que foi ajustado e por quê]\n"
                "</analysis>\n\n"
                "<improved_prompt>\n"
                "[O prompt completo melhorado — todas as seções originais preservadas]\n"
                "</improved_prompt>\n\n"
                "<transcript>\n"
                "[A transcrição da conversa simulada]\n"
                "</transcript>"
            )

            final_user_msg = (
                f"PROMPT DO AGENTE:\n{payload.prompt_text}\n\n"
                f"TRANSCRIÇÃO DO TESTE:\n{transcript}\n\n"
                "Melhore o prompt preservando TODO o conteúdo original. "
                "Use os delimitadores <analysis>, <improved_prompt> e <transcript>."
            )

            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.admin_openrouter_key}",
                    "HTTP-Referer": "https://axenwp.com",
                    "X-Title": "AxenWP Admin Prompt Analyzer",
                },
                json={
                    "model": settings.admin_openrouter_model or "openai/gpt-4o",
                    "max_tokens": 8000,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": final_user_msg}
                    ]
                }
            )

            if resp.status_code != 200:
                return {"success": False, "error": f"IA Mestre falhou ({resp.status_code}): {resp.text}"}

            import re
            raw_content = resp.json()["choices"][0]["message"]["content"]

            def extract_tag(tag: str) -> str:
                m = re.search(rf"<{tag}>(.*?)</{tag}>", raw_content, re.DOTALL)
                return m.group(1).strip() if m else ""

            analysis_text = extract_tag("analysis")
            improved      = extract_tag("improved_prompt")
            sim_transcript = extract_tag("transcript") or transcript

            if not analysis_text and not improved:
                logger.error(f"Delimitadores não encontrados na resposta. Raw: {raw_content[:500]}")
                return {"success": False, "error": "Resposta malformada da IA Mestre."}

            return {
                "success": True,
                "analysis": analysis_text,
                "improved_prompt": improved,
                "simulation_transcript": sim_transcript,
            }
    except Exception as e:
        logger.error(f"Erro no analisador de prompt: {e}", exc_info=True)
        return {"success": False, "error": "Erro interno do servidor ao tentar analisar."}
    finally:
        db.close()

@router.post("/{location_id}/test")
async def test_ai_agent(location_id: str, payload: dict):
    """
    Endpoint manual para o Testador de Chat no dashboard.
    Recebe message e o prompt/modelo atual (mesmo sem salvar).
    """
    db = SessionLocal()
    try:
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
        logger.error(f"Erro no chat tester: {e}")
        return {"success": False, "error": str(e)}
    finally:
        db.close()


