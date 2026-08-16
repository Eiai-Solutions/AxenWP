"""
Entrevista da IA Mestre: o loop de tool-use que gera o `form_data`.

Cliente Anthropic é falso — testamos a ORQUESTRAÇÃO (turnos, conclusão, guards,
tetos, serialização entre requests), não a API. O que importa aqui:

- a entrevista termina quando a Mestre decide, não num contador de perguntas;
- concluir sem campo obrigatório é RECUSADO por código, não só pedido no prompt;
- o estado sobrevive à ida e volta do banco com o par tool_use/tool_result
  encostado — `tool_use` órfão vira 400 em cascata na próxima chamada;
- o link público é anônimo e gasta a NOSSA chave, então o teto é testado.
"""

from types import SimpleNamespace

import pytest

import services.master_interview as mi
from services.master_interview import (
    EntrevistaIndisponivel,
    EstadoEntrevista,
    avancar,
    historico_visivel,
    ultima_fala,
)


# ── Fakes da API Anthropic ──

def _texto(t):
    return SimpleNamespace(type="text", text=t, model_dump=lambda: {"type": "text", "text": t})


def _tool(bid, nome, entrada):
    return SimpleNamespace(
        type="tool_use", id=bid, name=nome, input=entrada,
        model_dump=lambda: {"type": "tool_use", "id": bid, "name": nome, "input": entrada},
    )


def _uso(inp=500, out=60, cache_read=0):
    return SimpleNamespace(
        input_tokens=inp, output_tokens=out,
        cache_read_input_tokens=cache_read, cache_creation_input_tokens=0,
    )


class FakeResp:
    def __init__(self, content, stop_reason="end_turn", usage=None):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = usage or _uso()


class FakeClient:
    def __init__(self, roteiro):
        self._roteiro = list(roteiro)
        self.chamadas = []
        self.messages = self

    async def create(self, **kw):
        self.chamadas.append(kw)
        return self._roteiro.pop(0)


@pytest.fixture(autouse=True)
def _mestre_configurada(monkeypatch):
    monkeypatch.setattr(mi, "_resolve_master_key", lambda: "sk-fake", raising=True)
    monkeypatch.setattr(mi, "_read_settings", lambda: ("anthropic", None), raising=True)


def _instalar(monkeypatch, roteiro) -> FakeClient:
    cliente = FakeClient(roteiro)
    monkeypatch.setattr(
        mi, "AsyncAnthropic", lambda **kw: cliente, raising=False
    )
    import anthropic
    monkeypatch.setattr(anthropic, "AsyncAnthropic", lambda **kw: cliente, raising=True)
    return cliente


COMPLETO = {
    "company_name": "Pizzaria do Zé",
    "products_services": "pizza e esfiha",
    "agent_goal": "tirar duvida e anotar pedido",
    "tone": "informal",
}


# ── Condução ──

@pytest.mark.asyncio
async def test_primeira_fala_da_mestre_nao_exige_mensagem_do_usuario(monkeypatch):
    _instalar(monkeypatch, [FakeResp([_texto("Oi! O que a sua empresa faz?")])])

    estado = await avancar(EstadoEntrevista(), None)

    assert estado.concluida is False
    assert "o que a sua empresa faz" in ultima_fala(estado).lower()
    assert estado.turnos == 1


@pytest.mark.asyncio
async def test_a_entrevista_avanca_turno_a_turno(monkeypatch):
    _instalar(monkeypatch, [
        FakeResp([_texto("O que voces vendem?")]),
        FakeResp([_texto("E qual o horario de atendimento?")]),
    ])

    estado = await avancar(EstadoEntrevista(), None)
    estado = await avancar(estado, "vendo pizza")

    assert estado.turnos == 2
    assert "horario" in ultima_fala(estado).lower()
    assert estado.concluida is False


@pytest.mark.asyncio
async def test_concluir_entrega_o_form_data(monkeypatch):
    _instalar(monkeypatch, [
        FakeResp([_tool("t1", "concluir_entrevista", COMPLETO)], stop_reason="tool_use"),
    ])

    estado = await avancar(EstadoEntrevista(), "pode gerar")

    assert estado.concluida is True
    assert estado.form_data["company_name"] == "Pizzaria do Zé"
    assert estado.form_data["agent_goal"] == "tirar duvida e anotar pedido"


@pytest.mark.asyncio
async def test_avancar_numa_entrevista_ja_concluida_e_no_op(monkeypatch):
    cliente = _instalar(monkeypatch, [])
    estado = EstadoEntrevista(concluida=True, form_data=dict(COMPLETO))

    assert (await avancar(estado, "mais uma coisa")).form_data == COMPLETO
    assert cliente.chamadas == [], "chamou a API numa entrevista encerrada"


# ── Guards de código (o modelo é empírico) ──

@pytest.mark.asyncio
async def test_concluir_sem_campo_obrigatorio_e_recusado_e_a_mestre_continua(monkeypatch):
    """
    O prompt PEDE os obrigatórios, mas pedir não é garantir. Sem esta trava o
    agente nasceria genérico e o cliente odiaria a primeira versão.
    """
    _instalar(monkeypatch, [
        FakeResp([_tool("t1", "concluir_entrevista", {"company_name": "Só o nome"})],
                 stop_reason="tool_use"),
        FakeResp([_texto("Antes de fechar: o que voces vendem?")]),
    ])

    estado = await avancar(EstadoEntrevista(), "manda ver")

    assert estado.concluida is False, "aceitou concluir sem obrigatorio"
    assert estado.form_data is None
    assert "vendem" in ultima_fala(estado).lower()

    # O tool_result de erro precisa estar encostado no tool_use, senão a próxima
    # chamada estoura 400 por `tool_use` órfão.
    papeis = [m["role"] for m in estado.mensagens]
    assert papeis[-3:] == ["assistant", "user", "assistant"]
    resultado = estado.mensagens[-2]["content"][0]
    assert resultado["type"] == "tool_result" and resultado["is_error"] is True


@pytest.mark.asyncio
async def test_campos_desconhecidos_e_vazios_sao_descartados(monkeypatch):
    _instalar(monkeypatch, [
        FakeResp([_tool("t1", "concluir_entrevista",
                        {**COMPLETO, "campo_inventado": "x", "website": "   ", "faq": None})],
                 stop_reason="tool_use"),
    ])

    estado = await avancar(EstadoEntrevista(), "ok")

    assert "campo_inventado" not in estado.form_data
    assert "website" not in estado.form_data, "string vazia virou campo"
    assert "faq" not in estado.form_data


@pytest.mark.asyncio
async def test_ferramenta_desconhecida_nao_quebra_o_loop(monkeypatch):
    _instalar(monkeypatch, [
        FakeResp([_tool("t1", "ferramenta_que_nao_existe", {})], stop_reason="tool_use"),
        FakeResp([_texto("Desculpe, vamos continuar. Qual o horario?")]),
    ])

    estado = await avancar(EstadoEntrevista(), "oi")
    assert estado.concluida is False
    assert "horario" in ultima_fala(estado).lower()


# ── Custo e tetos (o link público é anônimo e gasta a nossa chave) ──

@pytest.mark.asyncio
async def test_prefixo_estavel_e_marcado_para_cache(monkeypatch):
    """Sem cache_control no prefixo, cada turno paga o system inteiro de novo."""
    cliente = _instalar(monkeypatch, [
        FakeResp([_texto("a")]), FakeResp([_texto("b")]),
    ])

    estado = await avancar(EstadoEntrevista(), None)
    await avancar(estado, "resposta")

    for chamada in cliente.chamadas:
        bloco = chamada["system"][0]
        assert bloco["cache_control"] == {"type": "ephemeral"}
    assert cliente.chamadas[0]["system"] == cliente.chamadas[1]["system"], \
        "prefixo variou entre turnos — invalida o cache silenciosamente"


@pytest.mark.asyncio
async def test_tokens_sao_acumulados_para_auditoria(monkeypatch):
    _instalar(monkeypatch, [
        FakeResp([_texto("a")], usage=_uso(inp=1000, out=50, cache_read=0)),
        FakeResp([_texto("b")], usage=_uso(inp=1200, out=70, cache_read=900)),
    ])

    estado = await avancar(EstadoEntrevista(), None)
    estado = await avancar(estado, "x")

    assert estado.tokens_saida == 120
    assert estado.tokens_cache_read == 900


@pytest.mark.asyncio
async def test_teto_de_turnos_interrompe(monkeypatch):
    _instalar(monkeypatch, [])
    estado = EstadoEntrevista(turnos=mi.MAX_TURNOS)

    with pytest.raises(EntrevistaIndisponivel):
        await avancar(estado, "mais")


@pytest.mark.asyncio
async def test_teto_de_tokens_interrompe(monkeypatch):
    _instalar(monkeypatch, [])
    estado = EstadoEntrevista(tokens_saida=mi.MAX_TOKENS_SAIDA)

    with pytest.raises(EntrevistaIndisponivel):
        await avancar(estado, "mais")


@pytest.mark.asyncio
async def test_sem_chave_da_mestre_recusa_com_mensagem_util(monkeypatch):
    monkeypatch.setattr(mi, "_resolve_master_key", lambda: None, raising=True)

    with pytest.raises(EntrevistaIndisponivel, match="não configurada"):
        await avancar(EstadoEntrevista(), None)


# ── Estado atravessa o banco ──

@pytest.mark.asyncio
async def test_estado_sobrevive_a_serializacao_com_o_par_tool_encostado(monkeypatch):
    """
    Entre requests o estado vai para o banco como JSON. Se o `tool_use` for
    salvo sem o `tool_result` colado, a próxima chamada à API estoura 400.
    """
    _instalar(monkeypatch, [
        FakeResp([_tool("t1", "concluir_entrevista", {"company_name": "X"})],
                 stop_reason="tool_use"),
        FakeResp([_texto("O que voces vendem?")]),
    ])

    estado = await avancar(EstadoEntrevista(), "oi")
    revivido = EstadoEntrevista.from_json(estado.to_json())

    assert revivido.mensagens == estado.mensagens
    assert revivido.turnos == estado.turnos
    assert revivido.concluida is False

    pos_tool_use = [
        i for i, m in enumerate(revivido.mensagens)
        if m["role"] == "assistant"
        and any(isinstance(b, dict) and b.get("type") == "tool_use" for b in m["content"])
    ]
    for i in pos_tool_use:
        seguinte = revivido.mensagens[i + 1]
        assert seguinte["role"] == "user"
        assert seguinte["content"][0]["type"] == "tool_result", "tool_use ficou órfão"


def test_estado_ilegivel_nao_derruba_recomeça_limpo():
    assert EstadoEntrevista.from_json("{isso não é json").mensagens == []
    assert EstadoEntrevista.from_json(None).turnos == 0


# ── O que a tela mostra ──

@pytest.mark.asyncio
async def test_historico_visivel_esconde_o_gatilho_e_o_protocolo(monkeypatch):
    _instalar(monkeypatch, [
        FakeResp([_texto("Oi! O que a empresa faz?")]),
        FakeResp([_tool("t1", "concluir_entrevista", {"company_name": "X"})],
                 stop_reason="tool_use"),
        FakeResp([_texto("E o que voces vendem?")]),
    ])

    estado = await avancar(EstadoEntrevista(), None)
    estado = await avancar(estado, "vendo pizza")

    visivel = historico_visivel(estado)
    assert [m["de"] for m in visivel] == ["mestre", "usuario", "mestre"]
    assert "Vamos começar." not in [m["texto"] for m in visivel], "gatilho vazou para a tela"
    assert all("tool_result" not in m["texto"] for m in visivel)
