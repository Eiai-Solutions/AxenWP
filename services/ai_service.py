import logging
import re
import base64
import tempfile
import os
from typing import List, Optional
from datetime import datetime

import httpx
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, BaseMessage

from data.database import SessionLocal
from data.models import AIAgent, ChatHistory, Tenant

logger = logging.getLogger(__name__)


async def transcribe_audio(audio_url: str, groq_api_key: str) -> Optional[str]:
    """
    Baixa o áudio da URL (Z-API) e transcreve usando Groq Whisper (gratuito e rápido).
    Retorna o texto transcrito ou None em caso de erro.
    """
    try:
        # 1. Baixar o áudio da Z-API
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            audio_resp = await client.get(audio_url)
            if audio_resp.status_code != 200:
                logger.error(f"Erro ao baixar áudio da Z-API: status={audio_resp.status_code}")
                return None
            audio_bytes = audio_resp.content

        # 2. Salvar em arquivo temporário para enviar ao Groq
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        try:
            # 3. Enviar para Groq Whisper API (OpenAI-compatible)
            async with httpx.AsyncClient(timeout=60.0) as client:
                with open(tmp_path, "rb") as f:
                    resp = await client.post(
                        "https://api.groq.com/openai/v1/audio/transcriptions",
                        headers={"Authorization": f"Bearer {groq_api_key}"},
                        data={
                            "model": "whisper-large-v3",
                            "language": "pt",
                            "response_format": "text",
                        },
                        files={"file": ("audio.ogg", f, "audio/ogg")},
                    )

                if resp.status_code == 200:
                    transcription = resp.text.strip()
                    logger.info(f"Áudio transcrito com sucesso ({len(transcription)} chars): {transcription[:80]}...")
                    return transcription
                else:
                    logger.error(f"Erro na transcrição Groq Whisper: status={resp.status_code}, body={resp.text}")
                    return None
        finally:
            os.unlink(tmp_path)

    except Exception as e:
        logger.error(f"Exceção ao transcrever áudio: {e}")
        return None


def _contains_special_content(text: str) -> bool:
    """
    Verifica se o texto contém conteúdo que ficaria bugado em TTS:
    - URLs / links (https://, www., .com, .com.br)
    - Emails (@)
    - Valores monetários (R$ 1.500,00)
    - Números longos (telefone, CEP, CNPJ, CPF)
    - Endereços (Rua, Av., Avenida, etc.)
    """
    patterns = [
        r'https?://',                          # URLs
        r'www\.',                              # Links www
        r'\.[a-z]{2,3}\.br\b',                # Domínios .com.br, .org.br
        r'\b\w+\.(com|net|org|io|app)\b',     # Domínios genéricos
        r'@',                                  # Emails
        r'R\$\s*[\d.,]+',                      # Valores em reais: R$ 1.500,00
        r'\d{1,3}(?:\.\d{3})+,\d{2}',         # Formato brasileiro de número: 1.500,00
        r'\d{5}[\-]?\d{3}',                   # CEP: 01234-567 ou 01234567
        r'\d{2}[\.\-]?\d{3}[\.\-]?\d{3}[\/\-]?\d{4}[\-]?\d{2}',  # CNPJ
        r'\d{3}[\.\-]?\d{3}[\.\-]?\d{3}[\-]?\d{2}',              # CPF
        r'\(?\d{2}\)?\s*\d{4,5}[\-\s]?\d{4}', # Telefone: (11) 99999-9999
        r'\b(?:Rua|Av\.|Avenida|Alameda|Travessa|Praça|Rodovia|Estrada|R\.)\s',  # Endereços
    ]
    combined = '|'.join(patterns)
    return bool(re.search(combined, text, re.IGNORECASE))

class PostgresChatMessageHistory:
    """Implementa o histórico de mensagens direto via SQLAlchemy (equivalente ao Postgres Chat Memory do n8n)."""
    
    def __init__(self, session_id: str):
        # A session_id idealmente será o location_id + telefone, por ex: "location123__+55119999999"
        self.session_id = session_id
        self.max_history = 20 # Mantém o contexto de no máximo N mensagens

    @property
    def messages(self) -> List[BaseMessage]:
        db = SessionLocal()
        try:
            # Pega as últimas N mensagens
            records = db.query(ChatHistory).filter(
                ChatHistory.session_id == self.session_id
            ).order_by(ChatHistory.id.desc()).limit(self.max_history).all()
            
            # Reverte para ficar em ordem cronológica p/ o modelo
            records.reverse()
            
            msgs = []
            for r in records:
                if r.message_type == "human":
                    msgs.append(HumanMessage(content=r.content))
                elif r.message_type == "ai":
                    msgs.append(AIMessage(content=r.content))
            return msgs
        finally:
            db.close()
            
    def add_user_message(self, message: str) -> None:
        self._add_message("human", message)
        
    def add_ai_message(self, message: str) -> None:
        self._add_message("ai", message)
        
    def _add_message(self, type_: str, content: str) -> None:
        db = SessionLocal()
        try:
            history = ChatHistory(
                session_id=self.session_id,
                message_type=type_,
                content=content
            )
            db.add(history)
            db.commit()
        except Exception as e:
            logger.error(f"Erro ao salvar mensagem no histórico: {e}")
            db.rollback()
        finally:
            db.close()


class AIEngine:
    """Core do motor IA integrando OpenRouter via LangChain e Memória persistente PostgreSQL."""
    
    def __init__(self, agent_data: AIAgent):
        self.agent_config = agent_data
        # Inicializa o LLM via OpenRouter usando a biblioteca oficial OpenAI com base_url
        # (já que OpenRouter é OpenAI-compatible)
        
        # Só inicializa se tiver chave
        self.llm = None
        if self.agent_config.api_key:
            try:
                self.llm = ChatOpenAI(
                    model=self.agent_config.model,
                    api_key=self.agent_config.api_key,
                    base_url="https://openrouter.ai/api/v1",
                    temperature=0.3,
                    max_tokens=1000,
                    model_kwargs={
                        # Headers extras úteis no OpenRouter (opcional mas recomendado)
                        "extra_headers": {
                            "HTTP-Referer": "https://axenwp.com",
                            "X-Title": "AxenWP IA Engine"
                        }
                    }
                )
            except Exception as e:
                logger.error(f"Erro ao instanciar LLM OpenRouter: {e}")

    async def generate_response(
        self, user_phone: str, user_message: str,
        is_audio: bool = False, audio_url: Optional[str] = None,
    ) -> Optional[dict]:
        """
        Recebe a mensagem do usuário, busca o histórico e gera a resposta com o LLM.
        Retorna um dicionário: {"type": "text"|"audio", "content": <string ou base64>, "text": <str>}
        """
        if not self.agent_config.is_active or not self.llm:
            logger.info("Agente IA inativo ou sem API Key configurada. Ignorando processamento cognitivo.")
            return None

        # ── Transcrição de áudio (STT) ──
        actual_message = user_message
        if is_audio and audio_url and self.agent_config.groq_api_key:
            logger.info(f"Áudio recebido de {user_phone}. Transcrevendo via Groq Whisper...")
            transcription = await transcribe_audio(audio_url, self.agent_config.groq_api_key)
            if transcription:
                actual_message = transcription
            else:
                logger.warning("Falha na transcrição. Usando mensagem original como fallback.")
        elif is_audio and not self.agent_config.groq_api_key:
            logger.warning("Áudio recebido mas Groq API Key não configurada. Ignorando transcrição.")

        # Identificador único de sessão de memória
        session_id = f"{self.agent_config.location_id}_{user_phone}"
        memory = PostgresChatMessageHistory(session_id)

        # Recupera histórico
        past_messages = memory.messages
        logger.info(f"Histórico carregado para {user_phone}: {len(past_messages)} mensagens (session: {session_id})")

        # Monta as mensagens diretamente (sem template string) para evitar
        # conflito com {} no prompt do usuário
        messages_for_llm: list[BaseMessage] = [
            SystemMessage(content=self.agent_config.prompt),
            *past_messages,
            HumanMessage(content=actual_message),
        ]

        try:
            # Invoca o LLM de forma assíncrona para não bloquear o event loop
            response = await self.llm.ainvoke(messages_for_llm)

            ai_text = response.content

            # Se deu certo, salva ambas as mensagens no banco (Humano + IA)
            memory.add_user_message(actual_message)
            memory.add_ai_message(ai_text)
            logger.info(f"Histórico salvo para {user_phone}: user='{actual_message[:50]}...' ai='{ai_text[:50]}...'")

            # ── Decisão: responder com áudio ou texto ──
            # Regra: cliente mandou áudio → responde áudio / cliente mandou texto → responde texto
            should_send_audio = is_audio

            # Exceção: fallback para texto se a resposta contém conteúdo especial
            # (R$, URLs, endereços, CPF, CNPJ, telefone, etc.)
            if should_send_audio and _contains_special_content(ai_text):
                logger.info("Resposta contém conteúdo especial (R$, URL, endereço, etc.). Fallback para texto.")
                should_send_audio = False

            # ── Gerar áudio via ElevenLabs (TTS) ──
            if should_send_audio and self.agent_config.elevenlabs_api_key and self.agent_config.elevenlabs_voice_id:
                try:
                    logger.info(f"Gerando áudio via ElevenLabs (VoiceID: {self.agent_config.elevenlabs_voice_id})...")
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        # output_format=ogg_opus → formato nativo de mensagem de voz do WhatsApp (PTT com ondas)
                        speed = float(self.agent_config.elevenlabs_speed or 1.0)
                        stability = float(self.agent_config.elevenlabs_stability or 0.5)
                        similarity = float(self.agent_config.elevenlabs_similarity or 0.75)

                        response_el = await client.post(
                            f"https://api.elevenlabs.io/v1/text-to-speech/{self.agent_config.elevenlabs_voice_id}?output_format=ogg_opus",
                            headers={
                                "xi-api-key": self.agent_config.elevenlabs_api_key,
                                "Content-Type": "application/json"
                            },
                            json={
                                "text": ai_text,
                                "model_id": "eleven_multilingual_v2",
                                "voice_settings": {
                                    "stability": stability,
                                    "similarity_boost": similarity,
                                    "speed": speed,
                                }
                            }
                        )

                        if response_el.status_code == 200:
                            audio_content = response_el.content
                            b64_audio = base64.b64encode(audio_content).decode("utf-8")
                            return {"type": "audio", "content": f"data:audio/ogg;base64,{b64_audio}", "text": ai_text}
                        else:
                            logger.error(f"Erro ao gerar ElevenLabs: {response_el.text}. Fallback texto.")
                except Exception as ex_el:
                    logger.error(f"Exceção no ElevenLabs: {ex_el}. Fallback texto.")

            # Resposta Padrão de Texto
            return {"type": "text", "content": ai_text}

        except Exception as e:
            logger.error(f"Erro ao gerar resposta do Agente IA: {e}")
            return None

# Serviço singleton para instanciar/invocações fáceis
class AIService:
    # Cache: location_id -> (updated_at, AIEngine)
    _engine_cache: dict = {}

    def get_agent_for_tenant(self, location_id: str) -> Optional[AIEngine]:
        db = SessionLocal()
        try:
            agent = db.query(AIAgent).filter(AIAgent.location_id == location_id).first()
            if not agent or not agent.is_active or not agent.api_key:
                self._engine_cache.pop(location_id, None)
                return None

            # Retorna do cache se o agente não foi alterado desde a última vez
            cached = self._engine_cache.get(location_id)
            if cached and cached[0] == agent.updated_at:
                return cached[1]

            engine = AIEngine(agent)
            self._engine_cache[location_id] = (agent.updated_at, engine)
            return engine
        finally:
            db.close()
            
    async def process_incoming_message(
        self, location_id: str, remote_jid: str, text_content: str,
        is_audio: bool = False, audio_url: Optional[str] = None,
    ) -> Optional[dict]:
        """
        Gatilho unificado. Executa o Agente caso o inquilino tenha ativado e retorna um dict p/ zapi_receiver.
        """
        engine = self.get_agent_for_tenant(location_id)
        if not engine:
            return None

        # O JID geralmente vem no formato '5511... @s.whatsapp.net', limpar
        phone_number = remote_jid.split('@')[0] if '@' in remote_jid else remote_jid

        return await engine.generate_response(
            user_phone=phone_number,
            user_message=text_content,
            is_audio=is_audio,
            audio_url=audio_url,
        )

ai_service = AIService()
