"""
Motor de IA dos agentes.

Após o refactor, este módulo contém apenas a orquestração:
- AIEngine: prepara prompt, chama LLM, aplica guardrails, decide TTS, retorna result.
- AIService: cache de engines por (location_id, channel) + roteamento de mensagens.

Lógica detalhada está distribuída em:
  services.audio_handler         (STT / TTS / heurística de conteúdo especial)
  services.chat_memory           (PostgresChatMessageHistory)
  services.qualification_engine  (extração + cache + summary)
  services.prompt_builder        (build_system_prompt)
  services.usage_logger          (save_usage_log)
"""

import asyncio
from collections import deque
from typing import Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from data.database import SessionLocal
from data.models import AIAgent
from utils.guardrails import (
    contains_forbidden_phrase,
    contains_placeholder,
    should_escalate as check_escalation,
    strip_emojis,
)
from utils.logger import logger
from utils import metrics

from services.audio_handler import (
    contains_special_content,
    resolve_groq_key,
    synthesize_for_agent,
    transcribe_audio,
)
from services.agent_engine import AgentContext, LangChainAgentEngine
from services.chat_memory import PostgresChatMessageHistory, make_session_id
from services.prompt_builder import build_system_prompt
from services.qualification_engine import (
    extract_qualification_data,
    generate_summary,
    is_already_qualified_sync,
)
from services.usage_logger import save_usage_log


# ─────────────────────────────────────────────────────────────────────
# Buffer dos últimos processamentos (debug via /admin/diagnostics/processings)
# ─────────────────────────────────────────────────────────────────────
_RECENT_PROCESSINGS_MAX = 30
_recent_processings: deque = deque(maxlen=_RECENT_PROCESSINGS_MAX)


def get_recent_processings() -> list:
    return list(_recent_processings)


def _record_processing(entry: dict):
    import time
    entry["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _recent_processings.append(entry)


# ─────────────────────────────────────────────────────────────────────
# AIEngine
# ─────────────────────────────────────────────────────────────────────

class AIEngine:
    """Núcleo do motor IA — orquestra prompt + LLM + guardrails + TTS."""

    def __init__(self, agent_data: AIAgent):
        self.agent_config = agent_data
        self.qualification_enabled = bool(getattr(agent_data, "qualification_enabled", False))
        self.qualification_fields = getattr(agent_data, "qualification_fields", None) or []

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
                        "extra_headers": {
                            "HTTP-Referer": "https://axenwp.com",
                            "X-Title": "AxenWP IA Engine",
                        }
                    },
                )
            except Exception as e:
                logger.error(f"Erro ao instanciar LLM OpenRouter: {e}")

        # Motor de agente (porta AgentEngine). Flag por-agente `agent_engine`
        # seleciona a implementação; no PR#1 só existe 'langchain' (default).
        # 'claude' (tool-use) entra no PR#2 e cai aqui em LangChain até lá.
        self.engine_name = (getattr(agent_data, "agent_engine", None) or "langchain").lower()
        self.engine = LangChainAgentEngine(self.llm) if self.llm is not None else None

    # ── Etapas internas ──

    async def _maybe_transcribe_audio(
        self, is_audio: bool, audio_url: Optional[str], user_message: str
    ) -> str:
        """Se for áudio e tiver chave/url, transcreve. Caso contrário retorna o user_message."""
        if not is_audio:
            return user_message

        groq_key = resolve_groq_key(self.agent_config.groq_api_key)
        logger.info(
            f"[AUDIO] is_audio=True | url={'sim' if audio_url else 'NÃO'} | "
            f"groq_key={'agente' if self.agent_config.groq_api_key else ('global' if groq_key else 'NENHUMA')}"
        )

        if not audio_url:
            logger.error("[AUDIO] is_audio=True mas audio_url vazia.")
            return user_message
        if not groq_key:
            logger.error("[AUDIO] is_audio=True mas nenhuma Groq API Key disponível.")
            return user_message

        logger.info(f"[AUDIO] Transcrevendo {audio_url[:80]}...")
        transcription = await transcribe_audio(audio_url, groq_key)
        if not transcription:
            logger.warning("[AUDIO] Transcrição retornou None — fallback texto.")
            return user_message

        try:
            await asyncio.to_thread(
                save_usage_log,
                location_id=self.agent_config.location_id,
                service="groq",
                model="whisper-large-v3",
            )
        except Exception as e_log:
            logger.warning(f"Falha usage log Groq: {e_log}")

        logger.info(f"[AUDIO] Transcrição OK: {transcription[:120]}")
        return transcription

    async def _apply_response_guardrails(
        self, ai_text: str, system_prompt: str
    ) -> str:
        """Aplica strip_emojis + regenera se houver placeholder ou frase outbound proibida."""
        ai_text = strip_emojis(ai_text)

        # Placeholders não-resolvidos
        placeholder = contains_placeholder(ai_text)
        if placeholder:
            logger.warning(f"Resposta com placeholder ({placeholder}). Regenerando...")
            try:
                regen = await self.llm.ainvoke([
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=(
                        f"A resposta abaixo veio com placeholder literal não resolvido: "
                        f"'{placeholder}'. Reescreva usando os dados reais da empresa "
                        "que estão no system prompt. JAMAIS use [PLACEHOLDER], {nome}, "
                        f"<X> etc. Resposta a corrigir: {ai_text}"
                    )),
                ])
                ai_text = strip_emojis(regen.content)
            except Exception as e:
                logger.warning(f"Falha ao regenerar placeholder: {e}")

        # Frases proibidas em modo outbound
        form_data = getattr(self.agent_config, "form_data", None) or {}
        if form_data.get("agent_type") == "outbound":
            forbidden = contains_forbidden_phrase(ai_text, "outbound")
            if forbidden:
                logger.warning(f"Resposta outbound com frase proibida ({forbidden}). Regenerando...")
                regen_prompt = (
                    "Reescreva a mensagem ABAIXO sem usar frases tipo 'como posso ajudar', "
                    "'tudo bem', 'em que posso ser útil'. Use o tom OUTBOUND — pergunta "
                    "direta sobre o produto/dor, não oferta de ajuda. Retorne APENAS a "
                    f"mensagem reescrita.\n\nMensagem original: {ai_text}"
                )
                try:
                    regen = await self.llm.ainvoke([
                        SystemMessage(content=system_prompt),
                        HumanMessage(content=regen_prompt),
                    ])
                    ai_text = regen.content
                except Exception as e:
                    logger.warning(f"Falha regen outbound: {e}")

        return ai_text

    async def _log_openrouter_usage(self, response) -> None:
        """Persiste tokens consumidos do OpenRouter na tabela usage_logs."""
        try:
            usage = getattr(response, "usage_metadata", None) or {}
            if isinstance(usage, dict):
                in_tok = usage.get("input_tokens", 0)
                out_tok = usage.get("output_tokens", 0)
            else:
                in_tok = getattr(usage, "input_tokens", 0)
                out_tok = getattr(usage, "output_tokens", 0)
            await asyncio.to_thread(
                save_usage_log,
                location_id=self.agent_config.location_id,
                service="openrouter",
                model=self.agent_config.model,
                input_tokens=in_tok,
                output_tokens=out_tok,
            )
        except Exception as e_log:
            logger.warning(f"Falha usage log OpenRouter: {e_log}")

    async def _maybe_generate_audio_reply(
        self, ai_text: str, is_audio_input: bool, processing_entry: dict
    ) -> Optional[str]:
        """Decide se gera TTS. Retorna data URL do áudio se sucesso, None caso contrário."""
        special = contains_special_content(ai_text)
        provider = (getattr(self.agent_config, "tts_provider", "elevenlabs") or "elevenlabs").lower()
        if provider == "fishaudio":
            has_key = bool(self.agent_config.fishaudio_api_key)
            has_voice = bool(self.agent_config.fishaudio_voice_id)
        else:
            has_key = bool(self.agent_config.elevenlabs_api_key)
            has_voice = bool(self.agent_config.elevenlabs_voice_id)

        processing_entry["special_content_in_reply"] = special
        processing_entry["tts_provider"] = provider
        processing_entry["has_tts_key"] = has_key
        processing_entry["has_tts_voice"] = has_voice

        logger.info(
            f"[TTS-DECISION] is_audio={is_audio_input} | provider={provider} | "
            f"has_key={has_key} | has_voice={has_voice} | special_content={special}"
        )

        if not is_audio_input or special or not has_key or not has_voice:
            if is_audio_input and special:
                logger.info(f"[TTS-DECISION] Fallback texto (conteúdo especial): {ai_text[:200]}")
            return None

        processing_entry["tts_attempted"] = True
        try:
            data_url = await synthesize_for_agent(text=ai_text, agent_config=self.agent_config)
            if data_url:
                processing_entry["tts_status"] = "ok"
                return data_url
            processing_entry["tts_status"] = f"{provider}_failed"
        except Exception as ex:
            logger.error(f"Exceção TTS ({provider}): {ex}")
            processing_entry["tts_status"] = f"exception: {ex}"
        return None

    # ── Entrypoint principal ──

    async def generate_response(
        self,
        user_phone: str,
        user_message: str,
        is_audio: bool = False,
        audio_url: Optional[str] = None,
    ) -> Optional[dict]:
        """Gera a resposta do agente para uma mensagem do lead."""
        if not self.agent_config.is_active or not self.llm:
            logger.info("Agente IA inativo ou sem API key. Ignorando.")
            return None

        # Lead já qualificado? Não responde mais (libera pra o humano).
        if self.qualification_enabled:
            already = await asyncio.to_thread(
                is_already_qualified_sync,
                self.agent_config.location_id,
                user_phone,
            )
            if already:
                logger.info(f"Lead {user_phone} já qualificado. IA pausada.")
                return None

        # 1. Transcreve áudio se for o caso (ou usa o texto recebido).
        actual_message = await self._maybe_transcribe_audio(is_audio, audio_url, user_message)

        # 2. Detecção de escalação humana / sentimento negativo.
        escalate, escalate_reason = check_escalation(actual_message)
        if escalate:
            logger.warning(f"Escalação detectada ({escalate_reason}) para {user_phone}")
            metrics.inc("axenwp_escalations_total", labels={"reason": escalate_reason or "unknown"})

        # 3. Carrega histórico da sessão.
        session_id = make_session_id(self.agent_config.location_id, user_phone)
        memory = PostgresChatMessageHistory(session_id)
        past_messages = await memory.aget_messages()
        logger.info(
            f"[MEMORY] session_id={session_id} | past={len(past_messages)} | raw_phone={user_phone!r}"
        )

        # 4. Monta system prompt (base + qualificação + modo áudio se for o caso).
        system_prompt = build_system_prompt(
            base_prompt=self.agent_config.prompt,
            qualification_enabled=self.qualification_enabled,
            qualification_fields=self.qualification_fields,
            is_audio_input=is_audio,
        )

        # Histórico neutro (engine-agnóstico) para a porta AgentEngine.
        history = [
            {"role": "assistant" if isinstance(m, AIMessage) else "user", "content": m.content}
            for m in past_messages
        ]
        ctx = AgentContext(
            location_id=self.agent_config.location_id,
            session_id=session_id,
            user_phone=user_phone,
            system_prompt=system_prompt,
            history=history,
            incoming_text=actual_message,
            channel=getattr(self.agent_config, "channel", "whatsapp"),
            is_audio_input=is_audio,
            agent_config=self.agent_config,
        )

        # 5. Chama o motor de agente (porta).
        try:
            turn = await self.engine.run(ctx)
            ai_text = turn.text
            metrics.inc("axenwp_ai_calls_total", labels={"model": self.agent_config.model})
        except Exception as e:
            logger.error(f"Erro na chamada do LLM: {e}")
            metrics.inc("axenwp_ai_calls_failed_total", labels={"model": self.agent_config.model})
            return None

        # 6. Guardrails de saída (emojis + placeholders + outbound).
        ai_text = await self._apply_response_guardrails(ai_text, system_prompt)

        # 7. Log de uso OpenRouter.
        await self._log_openrouter_usage(turn.raw)

        # 8. Qualificação (extrai marcador + gera resumo se completo).
        qualified_data = None
        qualification_summary = None
        if self.qualification_enabled and self.qualification_fields:
            ai_text, qualified_data = extract_qualification_data(
                ai_text, self.qualification_fields, session_id
            )
            if qualified_data:
                all_messages = list(past_messages) + [
                    HumanMessage(content=actual_message),
                    AIMessage(content=ai_text),
                ]
                qualification_summary = await generate_summary(
                    llm=self.llm,
                    past_messages=all_messages,
                    qualified_data=qualified_data,
                    location_id=self.agent_config.location_id,
                    model=self.agent_config.model,
                    custom_prompt=self.agent_config.qualification_summary_prompt,
                )

        # 9. Persiste no histórico.
        await memory.add_user_message(actual_message)
        await memory.add_ai_message(ai_text)
        logger.info(
            f"Histórico salvo para {user_phone}: user='{actual_message[:50]}...' ai='{ai_text[:50]}...'"
        )

        # 10. Decide áudio vs texto e gera TTS se for o caso.
        processing_entry: dict = {
            "location_id": self.agent_config.location_id,
            "phone": user_phone,
            "is_audio_input": is_audio,
            "special_content_in_reply": False,
            "ai_text_preview": ai_text[:200],
            "tts_attempted": False,
            "tts_status": None,
            "final_type": None,
        }

        audio_data_url = await self._maybe_generate_audio_reply(
            ai_text, is_audio, processing_entry
        )

        if audio_data_url:
            processing_entry["final_type"] = "audio"
            _record_processing(processing_entry)
            result = {"type": "audio", "content": audio_data_url, "text": ai_text}
        else:
            processing_entry["final_type"] = "text"
            if processing_entry["tts_status"] is None:
                processing_entry["tts_status"] = "skipped"
            _record_processing(processing_entry)
            result = {"type": "text", "content": ai_text}

        if qualified_data:
            result["qualified_data"] = qualified_data
            result["qualification_summary"] = qualification_summary
        if escalate:
            result["escalate"] = True
            result["escalate_reason"] = escalate_reason
        return result


# ─────────────────────────────────────────────────────────────────────
# AIService — cache de engines + roteamento por canal
# ─────────────────────────────────────────────────────────────────────

class AIService:
    # Cache: (location_id, channel) -> (updated_at, AIEngine)
    _engine_cache: dict = {}

    def _get_agent_for_tenant_sync(
        self, location_id: str, channel: str = "whatsapp"
    ) -> Optional[AIEngine]:
        db = SessionLocal()
        try:
            agent = (
                db.query(AIAgent)
                .filter(AIAgent.location_id == location_id, AIAgent.channel == channel)
                .first()
            )
            if not agent:
                self._engine_cache.pop((location_id, channel), None)
                return None

            # Alias: canal aponta pra outro canal e usa as configs de lá
            if getattr(agent, "linked_to_channel", None):
                target_channel = agent.linked_to_channel
                if target_channel != channel:
                    target = (
                        db.query(AIAgent)
                        .filter(
                            AIAgent.location_id == location_id,
                            AIAgent.channel == target_channel,
                        )
                        .first()
                    )
                    if target:
                        agent = target

            cache_key = (location_id, channel)
            if not agent.is_active or not agent.api_key:
                self._engine_cache.pop(cache_key, None)
                return None

            cached = self._engine_cache.get(cache_key)
            if cached and cached[0] == agent.updated_at:
                return cached[1]

            engine = AIEngine(agent)
            self._engine_cache[cache_key] = (agent.updated_at, engine)
            return engine
        finally:
            db.close()

    async def get_agent_for_tenant(
        self, location_id: str, channel: str = "whatsapp"
    ) -> Optional[AIEngine]:
        return await asyncio.to_thread(self._get_agent_for_tenant_sync, location_id, channel)

    async def process_incoming_message(
        self,
        location_id: str,
        remote_jid: str,
        text_content: str,
        is_audio: bool = False,
        audio_url: Optional[str] = None,
        channel: str = "whatsapp",
    ) -> Optional[dict]:
        """Entry point usado pelos webhooks para processar uma mensagem inbound."""
        engine = await self.get_agent_for_tenant(location_id, channel)
        if not engine:
            return None

        # JID Z-API: '5511...@s.whatsapp.net' -> '5511...'. Telegram: chat_id como string.
        phone_number = remote_jid.split("@")[0] if "@" in remote_jid else remote_jid

        return await engine.generate_response(
            user_phone=phone_number,
            user_message=text_content,
            is_audio=is_audio,
            audio_url=audio_url,
        )


ai_service = AIService()
