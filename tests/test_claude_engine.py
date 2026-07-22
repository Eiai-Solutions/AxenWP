"""
Motor Claude (tool-use): o loop model↔tools, caching e fallback.

Cliente Anthropic e tool_dispatch são falsos — testamos a orquestração do loop,
não a API. O engine não pode conhecer GHL/banco (efeitos ficam no dispatch).
"""

from types import SimpleNamespace

import pytest

from services.agent_engine.base import AgentContext, ToolSpec
from services.agent_engine.claude_engine import ClaudeAgentEngine
from services.agent_engine.tools import ESCALATE, QUALIFY, build_tool_specs


# ── Fakes da API Anthropic ──

def _text_block(text):
    return SimpleNamespace(type="text", text=text, model_dump=lambda: {"type": "text", "text": text})


def _tool_block(bid, name, inp):
    return SimpleNamespace(
        type="tool_use", id=bid, name=name, input=inp,
        model_dump=lambda: {"type": "tool_use", "id": bid, "name": name, "input": inp},
    )


def _usage(inp=100, out=20, cr=0, cw=0):
    return SimpleNamespace(
        input_tokens=inp, output_tokens=out,
        cache_read_input_tokens=cr, cache_creation_input_tokens=cw,
    )


class FakeResponse:
    def __init__(self, content, stop_reason, usage):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = usage


class FakeClient:
    """Devolve respostas roteirizadas, uma por chamada; registra o que recebeu."""
    def __init__(self, roteiro):
        self._roteiro = list(roteiro)
        self.calls = []
        self.messages = self  # client.messages.create

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._roteiro.pop(0)


def _ctx(**over):
    base = dict(
        location_id="loc1", session_id="loc1_5547", user_phone="5547",
        system_prompt="Você é um SDR.", history=[], incoming_text="oi",
        tools=[ToolSpec(name=ESCALATE, description="d", input_schema={"type": "object"})],
        tool_dispatch=None, max_tool_iterations=5, enable_prompt_cache=True,
    )
    base.update(over)
    return AgentContext(**base)


# ── Loop ──

@pytest.mark.asyncio
async def test_resposta_direta_sem_tool():
    client = FakeClient([FakeResponse([_text_block("Olá! Como posso ajudar?")], "end_turn", _usage())])
    turn = await ClaudeAgentEngine(client, model="claude-sonnet-5").run(_ctx())
    assert turn.text == "Olá! Como posso ajudar?"
    assert turn.stop_reason == "end_turn"
    assert turn.tool_calls == []
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_chama_tool_e_continua_ate_concluir():
    chamou = {}

    async def dispatch(name, args, ctx):
        chamou["name"] = name
        chamou["args"] = args
        return {"status": "ok"}

    client = FakeClient([
        FakeResponse([_tool_block("t1", ESCALATE, {"motivo": "pediu humano"})], "tool_use", _usage()),
        FakeResponse([_text_block("Já chamei um atendente.")], "end_turn", _usage(cr=90)),
    ])
    turn = await ClaudeAgentEngine(client).run(_ctx(tool_dispatch=dispatch))

    assert chamou == {"name": ESCALATE, "args": {"motivo": "pediu humano"}}
    assert turn.text == "Já chamei um atendente."
    assert [tc.name for tc in turn.tool_calls] == [ESCALATE]
    assert turn.tool_calls[0].result == {"status": "ok"}
    assert len(client.calls) == 2
    # 2ª chamada carrega o par assistant(tool_use)+user(tool_result) na lista local
    msgs2 = client.calls[1]["messages"]
    assert msgs2[-2]["role"] == "assistant"
    assert msgs2[-1]["content"][0]["type"] == "tool_result"


@pytest.mark.asyncio
async def test_erro_na_tool_vira_tool_result_nao_quebra_o_loop():
    async def dispatch(name, args, ctx):
        raise RuntimeError("GHL fora")

    client = FakeClient([
        FakeResponse([_tool_block("t1", QUALIFY, {"nome": "Luiz"})], "tool_use", _usage()),
        FakeResponse([_text_block("Tive um problema, vou verificar.")], "end_turn", _usage()),
    ])
    turn = await ClaudeAgentEngine(client).run(_ctx(tool_dispatch=dispatch))
    assert turn.text == "Tive um problema, vou verificar."
    assert turn.tool_calls[0].result["error"] == "GHL fora"


@pytest.mark.asyncio
async def test_sem_dispatch_devolve_erro_ao_modelo():
    client = FakeClient([
        FakeResponse([_tool_block("t1", ESCALATE, {})], "tool_use", _usage()),
        FakeResponse([_text_block("ok")], "end_turn", _usage()),
    ])
    turn = await ClaudeAgentEngine(client).run(_ctx(tool_dispatch=None))
    assert turn.tool_calls[0].result["error"].startswith("tool_dispatch")


@pytest.mark.asyncio
async def test_fallback_ao_estourar_iteracoes():
    # Sempre pede tool → nunca conclui; o cap protege e devolve fallback.
    loop = [FakeResponse([_tool_block(f"t{i}", ESCALATE, {})], "tool_use", _usage()) for i in range(10)]

    async def dispatch(name, args, ctx):
        return {"ok": True}

    turn = await ClaudeAgentEngine(FakeClient(loop)).run(_ctx(tool_dispatch=dispatch, max_tool_iterations=3))
    assert turn.stop_reason == "max_iterations"
    assert turn.text == ""


# ── Caching ──

@pytest.mark.asyncio
async def test_cache_control_no_prefixo_quando_habilitado():
    client = FakeClient([FakeResponse([_text_block("oi")], "end_turn", _usage())])
    await ClaudeAgentEngine(client).run(_ctx(enable_prompt_cache=True))
    system = client.calls[0]["system"]
    assert system[0]["cache_control"] == {"type": "ephemeral"}


@pytest.mark.asyncio
async def test_sem_cache_quando_desabilitado():
    client = FakeClient([FakeResponse([_text_block("oi")], "end_turn", _usage())])
    await ClaudeAgentEngine(client).run(_ctx(enable_prompt_cache=False))
    assert "cache_control" not in client.calls[0]["system"][0]


@pytest.mark.asyncio
async def test_usage_acumula_cache_read_para_o_log_de_custo():
    client = FakeClient([
        FakeResponse([_tool_block("t1", ESCALATE, {})], "tool_use", _usage(inp=4000, cw=4000)),
        FakeResponse([_text_block("ok")], "end_turn", _usage(inp=100, cr=4000)),
    ])

    async def dispatch(n, a, c):
        return {}

    turn = await ClaudeAgentEngine(client).run(_ctx(tool_dispatch=dispatch))
    assert turn.usage["cache_read_input_tokens"] == 4000
    assert turn.usage["cache_creation_input_tokens"] == 4000
    assert turn.usage["output_tokens"] == 40


@pytest.mark.asyncio
async def test_history_precede_a_mensagem_atual():
    client = FakeClient([FakeResponse([_text_block("oi")], "end_turn", _usage())])
    hist = [{"role": "user", "content": "bom dia"}, {"role": "assistant", "content": "bom dia!"}]
    await ClaudeAgentEngine(client).run(_ctx(history=hist, incoming_text="tudo bem?"))
    msgs = client.calls[0]["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[-1]["content"] == "tudo bem?"


# ── Specs das tools ──

def test_build_specs_so_escalate_sem_qualificacao():
    agent = SimpleNamespace(qualification_enabled=False, qualification_fields=None)
    specs = build_tool_specs(agent)
    assert [s.name for s in specs] == [ESCALATE]


def test_build_specs_inclui_qualify_com_campos_de_coleta():
    agent = SimpleNamespace(
        qualification_enabled=True,
        qualification_fields=[
            {"key": "nome", "label": "Nome", "auto": False},
            {"key": "telefone", "label": "Telefone", "auto": True},  # auto não vira propriedade
        ],
    )
    specs = build_tool_specs(agent)
    nomes = [s.name for s in specs]
    assert QUALIFY in nomes and ESCALATE in nomes
    qspec = next(s for s in specs if s.name == QUALIFY)
    assert "nome" in qspec.input_schema["properties"]
    assert "telefone" not in qspec.input_schema["properties"]  # auto excluído
    assert qspec.input_schema["required"] == ["nome"]


def test_build_specs_ordem_estavel_para_cache():
    agent = SimpleNamespace(qualification_enabled=True,
                            qualification_fields=[{"key": "nome", "label": "Nome"}])
    a = [s.name for s in build_tool_specs(agent)]
    b = [s.name for s in build_tool_specs(agent)]
    assert a == b  # determinístico entre requests
