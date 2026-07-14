"""
Caracterização da porta AgentEngine e do seam em AIEngine.generate_response.

Antes do PR#1 não havia teste exercitando o caminho de geração de resposta —
"suíte verde" era prova vazia de paridade. Estes testes travam o comportamento
do seam extraído (montagem das mensagens + shape do result dict) para que a
troca de motor (LangChain -> Claude no PR#2) não possa regredir sem quebrar.
"""

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from services.agent_engine import AgentContext, LangChainAgentEngine


# ─────────────────────────────────────────────────────────────────────
# Dublês
# ─────────────────────────────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, content):
        self.content = content
        self.usage_metadata = {"input_tokens": 3, "output_tokens": 4}


class _FakeLLM:
    """Registra as mensagens recebidas e devolve uma resposta fixa."""

    def __init__(self, content="Resposta da IA"):
        self._content = content
        self.calls = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return _FakeResponse(self._content)


class _FakeMemory:
    """Substitui PostgresChatMessageHistory sem tocar no banco."""

    _preset: list = []

    def __init__(self, session_id, *a, **k):
        self.session_id = session_id
        self.added = []

    async def aget_messages(self):
        return list(type(self)._preset)

    async def add_user_message(self, m):
        self.added.append(("human", m))

    async def add_ai_message(self, m):
        self.added.append(("ai", m))


def _agent_config():
    return SimpleNamespace(
        is_active=True,
        api_key="sk-test",
        model="test/model",
        location_id="loc1",
        channel="whatsapp",
        groq_api_key=None,
        qualification_enabled=False,
        qualification_fields=[],
        qualification_summary_prompt=None,
        prompt="Você é um atendente.",
        form_data={},
        tts_provider="elevenlabs",
        elevenlabs_api_key=None,
        elevenlabs_voice_id=None,
        fishaudio_api_key=None,
        fishaudio_voice_id=None,
    )


# ─────────────────────────────────────────────────────────────────────
# LangChainAgentEngine (a porta isolada)
# ─────────────────────────────────────────────────────────────────────

class TestLangChainAgentEngine:
    async def test_builds_system_history_human_sequence(self):
        llm = _FakeLLM("Oi!")
        engine = LangChainAgentEngine(llm)
        ctx = AgentContext(
            location_id="loc1",
            session_id="loc1_5511",
            user_phone="5511",
            system_prompt="PROMPT",
            history=[
                {"role": "user", "content": "primeira"},
                {"role": "assistant", "content": "resposta anterior"},
            ],
            incoming_text="mensagem nova",
        )

        turn = await engine.run(ctx)

        assert turn.text == "Oi!"
        assert turn.raw is not None  # resposta LangChain preservada p/ usage log

        (sent,) = llm.calls
        # System + 2 de histórico + Human atual, na ordem exata do código legado.
        assert isinstance(sent[0], SystemMessage) and sent[0].content == "PROMPT"
        assert isinstance(sent[1], HumanMessage) and sent[1].content == "primeira"
        assert isinstance(sent[2], AIMessage) and sent[2].content == "resposta anterior"
        assert isinstance(sent[3], HumanMessage) and sent[3].content == "mensagem nova"
        assert len(sent) == 4

    async def test_empty_history(self):
        llm = _FakeLLM()
        engine = LangChainAgentEngine(llm)
        ctx = AgentContext(
            location_id="loc1", session_id="loc1_x", user_phone="x",
            system_prompt="P", history=[], incoming_text="oi",
        )
        await engine.run(ctx)
        (sent,) = llm.calls
        assert len(sent) == 2
        assert isinstance(sent[0], SystemMessage)
        assert isinstance(sent[1], HumanMessage) and sent[1].content == "oi"


# ─────────────────────────────────────────────────────────────────────
# AIEngine.generate_response (o seam end-to-end, caminho texto)
# ─────────────────────────────────────────────────────────────────────

class TestGenerateResponseTextPath:
    async def test_text_reply_shape(self, monkeypatch):
        import services.ai_service as ai_mod

        _FakeMemory._preset = []
        monkeypatch.setattr(ai_mod, "PostgresChatMessageHistory", _FakeMemory)
        # Não tocar no banco no log de uso.
        monkeypatch.setattr(ai_mod, "save_usage_log", lambda **k: None)

        engine = ai_mod.AIEngine(_agent_config())
        # Substitui o LLM real (ChatOpenAI) por um dublê determinístico.
        fake = _FakeLLM("Olá, como vai?")
        engine.llm = fake
        engine.engine = LangChainAgentEngine(fake)

        result = await engine.generate_response(
            user_phone="5511999999999",
            user_message="Oi",
            is_audio=False,
        )

        # Caminho texto: sem TTS (is_audio=False), sem qualificação.
        assert result == {"type": "text", "content": "Olá, como vai?"}

        # A mensagem do lead entrou como Human no engine.
        (sent,) = fake.calls
        assert isinstance(sent[0], SystemMessage)
        assert isinstance(sent[-1], HumanMessage) and sent[-1].content == "Oi"

    async def test_inactive_agent_returns_none(self, monkeypatch):
        import services.ai_service as ai_mod
        cfg = _agent_config()
        cfg.is_active = False
        engine = ai_mod.AIEngine(cfg)
        fake = _FakeLLM()
        engine.llm = fake
        engine.engine = LangChainAgentEngine(fake)

        result = await engine.generate_response("5511", "Oi")
        assert result is None
        assert fake.calls == []  # nem chega a chamar o motor
