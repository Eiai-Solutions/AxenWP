import logging
from typing import List, Optional, Tuple
from datetime import datetime

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, BaseMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from data.database import SessionLocal
from data.models import AIAgent, ChatHistory, Tenant

logger = logging.getLogger(__name__)

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

    async def generate_response(self, user_phone: str, user_message: str, is_audio: bool = False) -> Optional[dict]:
        """
        Recebe a mensagem do usuário, busca o histórico e gera a resposta com o LLM.
        Retorna um dicionário: {"type": "text"|"audio", "content": <string ou base64>}
        """
        if not self.agent_config.is_active or not self.llm:
            logger.info("Agente IA inativo ou sem API Key configurada. Ignorando processamento cognitivo.")
            return None

        # Identificador único de sessão de memória
        session_id = f"{self.agent_config.location_id}_{user_phone}"
        memory = PostgresChatMessageHistory(session_id)

        # Recupera histórico
        past_messages = memory.messages

        # Constrói o template base (a "conciência" e o prompt)
        prompt_template = ChatPromptTemplate.from_messages([
            ("system", self.agent_config.prompt),
            MessagesPlaceholder(variable_name="history"),
            ("human", "{input}")
        ])

        chain = prompt_template | self.llm

        try:
            # Invoca o LLM de forma assíncrona para não bloquear o event loop
            response = await chain.ainvoke({
                "history": past_messages,
                "input": user_message
            })

            ai_text = response.content

            # Se deu certo, salva ambas as mensagens no banco (Humano + IA)
            memory.add_user_message(user_message)
            memory.add_ai_message(ai_text)

            # Lógica de Áudio (ElevenLabs)
            should_send_audio = False
            if self.agent_config.always_reply_with_audio or is_audio:
                should_send_audio = True

            # Regra de Exceção: Não enviar áudio se contiver links, emails, @, números grandes ou formatações que a IA fala mal.
            if should_send_audio:
                import re
                # Bloqueia audio se tiver: números longos (telefone/CEP/preço), URLs, @, links
                if re.search(r'(https?://|www\.|@|\d{4,})', ai_text, re.IGNORECASE):
                    logger.info("Resposta IA contém URLs, @ ou números longos. Fazendo fallback para Texto.")
                    should_send_audio = False

            if should_send_audio and self.agent_config.elevenlabs_api_key and self.agent_config.elevenlabs_voice_id:
                try:
                    import httpx
                    import base64
                    
                    logger.info(f"Gerando áudio via ElevenLabs (VoiceID: {self.agent_config.elevenlabs_voice_id})...")
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        response_el = await client.post(
                            f"https://api.elevenlabs.io/v1/text-to-speech/{self.agent_config.elevenlabs_voice_id}",
                            headers={
                                "xi-api-key": self.agent_config.elevenlabs_api_key,
                                "Content-Type": "application/json"
                            },
                            json={
                                "text": ai_text,
                                "model_id": "eleven_multilingual_v2",
                                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}
                            }
                        )
                        
                        if response_el.status_code == 200:
                            audio_content = response_el.content
                            b64_audio = base64.b64encode(audio_content).decode("utf-8")
                            # Retorna o dict avisando que é áudio (basta usar data:audio/mpeg;base64)
                            return {"type": "audio", "content": f"data:audio/mpeg;base64,{b64_audio}", "text": ai_text}
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
            
    async def process_incoming_message(self, location_id: str, remote_jid: str, text_content: str, is_audio: bool = False) -> Optional[dict]:
        """
        Gatilho unificado. Executa o Agente caso o inquilino tenha ativado e retorna um dict p/ zapi_receiver.
        """
        engine = self.get_agent_for_tenant(location_id)
        if not engine:
            return None

        # O JID geralmente vem no formato '5511... @s.whatsapp.net', limpar
        phone_number = remote_jid.split('@')[0] if '@' in remote_jid else remote_jid

        return await engine.generate_response(user_phone=phone_number, user_message=text_content, is_audio=is_audio)

ai_service = AIService()
