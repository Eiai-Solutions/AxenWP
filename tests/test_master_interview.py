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


def _uso(inp=500, out=60, cache_read=0, buscas=None):
    return SimpleNamespace(
        input_tokens=inp, output_tokens=out,
        cache_read_input_tokens=cache_read, cache_creation_input_tokens=0,
        server_tool_use=(SimpleNamespace(web_search_requests=buscas)
                         if buscas is not None else None),
    )


async def _async(valor):
    """Embrulha um valor num awaitable — para trocar as pesquisas por lambdas."""
    return valor


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


def test_contador_de_pesquisas_sobrevive_ao_banco():
    """
    O teto precisa valer na entrevista INTEIRA. Se `pesquisas` não atravessar a
    serialização, o contador zera a cada request e o teto deixa de existir.
    """
    revivido = EstadoEntrevista.from_json(EstadoEntrevista(pesquisas=4).to_json())

    assert revivido.pesquisas == 4
    assert revivido.pode_pesquisar is True
    assert EstadoEntrevista(pesquisas=mi.MAX_PESQUISAS).pode_pesquisar is False


# ── Pesquisa da empresa ──

@pytest.mark.asyncio
async def test_a_mestre_le_o_site_e_o_conteudo_chega_rotulado(monkeypatch):
    """
    O ponto da pesquisa: chegar sabendo. E o conteúdo precisa entrar como DADO —
    página é escrita por terceiro e quem lê é um LLM.
    """
    async def fake_site(url):
        assert url == "padariaaurora.com.br"
        return {"url_final": "https://padariaaurora.com.br/",
                "texto": "Padaria Aurora. Paes artesanais e bolos por encomenda.",
                "truncado": False, "saltos": []}
    monkeypatch.setattr(mi, "ler_site", fake_site, raising=True)

    _instalar(monkeypatch, [
        FakeResp([_tool("t1", "ler_site", {"url": "padariaaurora.com.br"})],
                 stop_reason="tool_use"),
        FakeResp([_texto("Vi que voces fazem paes artesanais. Quem e o cliente tipico?")]),
    ])

    estado = await avancar(EstadoEntrevista(), "meu site e padariaaurora.com.br")

    assert estado.pesquisas == 1
    assert "cliente tipico" in ultima_fala(estado).lower()

    resultado = estado.mensagens[-2]["content"][0]
    assert resultado["type"] == "tool_result"
    assert resultado.get("is_error") is not True
    assert resultado["content"].startswith("[CONTEÚDO DA PÁGINA")
    assert "Paes artesanais" in resultado["content"]


@pytest.mark.asyncio
async def test_a_mestre_consulta_o_cnpj(monkeypatch):
    async def fake_cnpj(cnpj):
        return {"razao_social": "PADARIA AURORA LTDA", "cidade": "SAO PAULO", "uf": "SP"}
    monkeypatch.setattr(mi, "consultar_cnpj", fake_cnpj, raising=True)

    _instalar(monkeypatch, [
        FakeResp([_tool("t1", "consultar_cnpj", {"cnpj": "11.222.333/0001-81"})],
                 stop_reason="tool_use"),
        FakeResp([_texto("Achei: Padaria Aurora, em Sao Paulo. O que voces vendem mais?")]),
    ])

    estado = await avancar(EstadoEntrevista(), "meu cnpj e 11.222.333/0001-81")

    assert estado.pesquisas == 1
    assert "PADARIA AURORA LTDA" in estado.mensagens[-2]["content"][0]["content"]


@pytest.mark.asyncio
async def test_site_fora_do_ar_nao_derruba_a_entrevista(monkeypatch):
    """
    Do outro lado tem um dono de negócio, não um operador. Site quebrado vira
    meia frase e a conversa segue — nunca uma exceção na cara dele.
    """
    async def fake_site(url):
        raise mi.PesquisaRecusada("O site respondeu 503.")
    monkeypatch.setattr(mi, "ler_site", fake_site, raising=True)

    _instalar(monkeypatch, [
        FakeResp([_tool("t1", "ler_site", {"url": "empresa.com"})], stop_reason="tool_use"),
        FakeResp([_texto("Nao consegui abrir o site. Me conta: o que voces vendem?")]),
    ])

    estado = await avancar(EstadoEntrevista(), "olha meu site: empresa.com")

    assert estado.concluida is False
    assert "vendem" in ultima_fala(estado).lower()
    resultado = estado.mensagens[-2]["content"][0]
    assert resultado["is_error"] is True and "503" in resultado["content"]


@pytest.mark.asyncio
async def test_erro_inesperado_na_pesquisa_tambem_e_contido(monkeypatch):
    """Qualquer exceção — não só `PesquisaRecusada` — vira tool_result."""
    async def explode(url):
        raise RuntimeError("boom")
    monkeypatch.setattr(mi, "ler_site", explode, raising=True)

    _instalar(monkeypatch, [
        FakeResp([_tool("t1", "ler_site", {"url": "empresa.com"})], stop_reason="tool_use"),
        FakeResp([_texto("Vamos seguir. O que voces vendem?")]),
    ])

    estado = await avancar(EstadoEntrevista(), "empresa.com")

    assert estado.mensagens[-2]["content"][0]["is_error"] is True
    assert "boom" not in estado.mensagens[-2]["content"][0]["content"], "vazou stacktrace"


@pytest.mark.asyncio
async def test_teto_de_pesquisas_impede_a_chamada(monkeypatch):
    """
    O link é público e anônimo: sem teto, ele é um proxy HTTP aberto rodando na
    NOSSA infra e gastando a NOSSA chave.
    """
    chamou = []
    async def fake_site(url):
        chamou.append(url)
        return {"url_final": url, "texto": "x", "truncado": False, "saltos": []}
    monkeypatch.setattr(mi, "ler_site", fake_site, raising=True)

    _instalar(monkeypatch, [
        FakeResp([_tool("t1", "ler_site", {"url": "a.com"})], stop_reason="tool_use"),
        FakeResp([_texto("Certo, vamos continuar sem o site.")]),
    ])
    estado = EstadoEntrevista(pesquisas=mi.MAX_PESQUISAS)

    estado = await avancar(estado, "olha esse tambem")

    assert chamou == [], "pesquisou depois do teto"
    assert estado.pesquisas == mi.MAX_PESQUISAS, "contador passou do teto"
    resultado = estado.mensagens[-2]["content"][0]
    assert resultado["is_error"] is True and "Limite de pesquisas" in resultado["content"]


@pytest.mark.asyncio
async def test_tentativa_que_falha_tambem_consome_o_teto(monkeypatch):
    """Senão um site quebrado em loop custa rede infinita de graça."""
    async def fake_site(url):
        raise mi.PesquisaRecusada("nao rolou")
    monkeypatch.setattr(mi, "ler_site", fake_site, raising=True)

    _instalar(monkeypatch, [
        FakeResp([_tool("t1", "ler_site", {"url": "a.com"})], stop_reason="tool_use"),
        FakeResp([_texto("segue o baile")]),
    ])

    estado = await avancar(EstadoEntrevista(), "a.com")
    assert estado.pesquisas == 1


@pytest.mark.asyncio
async def test_lista_de_tools_e_constante_mesmo_com_o_teto_estourado(monkeypatch):
    """
    Tirar as ferramentas do request quando o teto estoura invalidaria o prefixo
    cacheado E deixaria o histórico com `tool_use` de ferramenta não declarada —
    400 no meio da entrevista. O teto mora no dispatch, não na lista.
    """
    cliente = _instalar(monkeypatch, [
        FakeResp([_tool("t1", "ler_site", {"url": "a.com"})], stop_reason="tool_use"),
        FakeResp([_texto("ok")]),
    ])

    await avancar(EstadoEntrevista(pesquisas=mi.MAX_PESQUISAS), "a.com")

    nomes = [[t["name"] for t in c["tools"]] for c in cliente.chamadas]
    assert nomes[0] == nomes[1] == [
        "concluir_entrevista", "ler_site", "consultar_cnpj", "web_search",
    ]


@pytest.mark.asyncio
async def test_duas_pesquisas_no_mesmo_turno(monkeypatch):
    """CNPJ e site juntos — o caso comum de quem manda os dois de uma vez."""
    monkeypatch.setattr(mi, "consultar_cnpj",
                        lambda c: _async({"razao_social": "AURORA LTDA"}), raising=True)
    monkeypatch.setattr(mi, "ler_site",
                        lambda u: _async({"url_final": u, "texto": "Paes", "truncado": False}),
                        raising=True)

    _instalar(monkeypatch, [
        FakeResp([_tool("t1", "consultar_cnpj", {"cnpj": "11.222.333/0001-81"}),
                  _tool("t2", "ler_site", {"url": "aurora.com"})], stop_reason="tool_use"),
        FakeResp([_texto("Peguei tudo. Qual o horario de atendimento?")]),
    ])

    estado = await avancar(EstadoEntrevista(), "cnpj 11.222.333/0001-81, site aurora.com")

    assert estado.pesquisas == 2
    ids = [r["tool_use_id"] for r in estado.mensagens[-2]["content"]]
    assert ids == ["t1", "t2"], "tool_result fora de ordem ou faltando — vira 400"


@pytest.mark.asyncio
async def test_duas_pesquisas_rodam_ao_mesmo_tempo(monkeypatch):
    """
    Em série são dois timeouts empilhados (24s) com a pessoa olhando o
    "digitando...". As duas precisam estar no ar juntas.
    """
    import asyncio

    em_voo, pico = 0, 0

    async def lenta(*a, **k):
        nonlocal em_voo, pico
        em_voo += 1
        pico = max(pico, em_voo)
        await asyncio.sleep(0.02)
        em_voo -= 1
        return {"url_final": "x", "texto": "y", "truncado": False}

    monkeypatch.setattr(mi, "ler_site", lenta, raising=True)
    monkeypatch.setattr(mi, "consultar_cnpj", lenta, raising=True)
    _instalar(monkeypatch, [
        FakeResp([_tool("t1", "consultar_cnpj", {"cnpj": "1"}),
                  _tool("t2", "ler_site", {"url": "a.com"})], stop_reason="tool_use"),
        FakeResp([_texto("ok")]),
    ])

    await avancar(EstadoEntrevista(), "cnpj e site")

    assert pico == 2, f"as pesquisas rodaram em série (pico={pico})"


@pytest.mark.asyncio
async def test_concluir_junto_com_pesquisa_nao_deixa_coroutine_solta(monkeypatch):
    """
    Concluir retorna ANTES do gather. A coroutine da pesquisa já criada precisa
    ser fechada, senão vira "coroutine was never awaited" e conexão pendurada.
    """
    import warnings

    monkeypatch.setattr(mi, "ler_site",
                        lambda u: _async({"url_final": u, "texto": "x", "truncado": False}),
                        raising=True)
    _instalar(monkeypatch, [
        FakeResp([_tool("t1", "concluir_entrevista", COMPLETO),
                  _tool("t2", "ler_site", {"url": "a.com"})], stop_reason="tool_use"),
    ])

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        estado = await avancar(EstadoEntrevista(), "pode gerar")

    assert estado.concluida is True
    assert estado.form_data["company_name"] == "Pizzaria do Zé"


@pytest.mark.asyncio
async def test_historico_vai_marcado_para_cache_sem_sujar_o_estado(monkeypatch):
    """
    Com a busca na web o histórico ficou pesado (o resultado volta inteiro e é
    reenviado todo turno). O breakpoint anda no fim da conversa — mas numa CÓPIA:
    vazar `cache_control` para o estado sujaria o JSON do banco e espalharia marcas
    pelo histórico a cada turno.
    """
    cliente = _instalar(monkeypatch, [FakeResp([_texto("a")]), FakeResp([_texto("b")])])

    estado = await avancar(EstadoEntrevista(), None)
    estado = await avancar(estado, "Padaria Aurora, em Sao Paulo")

    enviadas = cliente.chamadas[1]["messages"]
    assert enviadas[-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert all("cache_control" not in b
               for m in enviadas[:-1] if isinstance(m["content"], list)
               for b in m["content"] if isinstance(b, dict)), \
        "marca ficou para trás — breakpoint tem que andar, não acumular"

    bruto = estado.to_json()
    assert "cache_control" not in bruto, "marca de transporte vazou para o banco"


@pytest.mark.asyncio
async def test_marcar_o_cache_nao_desencosta_o_par_tool_use_tool_result(monkeypatch):
    """A cópia não pode reordenar nem perder bloco — é onde este projeto já sangrou."""
    monkeypatch.setattr(mi, "ler_site",
                        lambda u: _async({"url_final": u, "texto": "Paes", "truncado": False}),
                        raising=True)
    _instalar(monkeypatch, [
        FakeResp([_tool("t1", "ler_site", {"url": "a.com"})], stop_reason="tool_use"),
        FakeResp([_texto("ok")]),
    ])

    estado = await avancar(EstadoEntrevista(), "a.com")

    enviadas = mi._mensagens_para_envio(estado.mensagens)
    assert [m["role"] for m in enviadas] == [m["role"] for m in estado.mensagens]
    for i, m in enumerate(estado.mensagens):
        assert len(enviadas[i]["content"]) == len(m["content"]) if isinstance(
            m["content"], list) else True


def test_marcar_cache_em_historico_vazio_nao_quebra():
    assert mi._mensagens_para_envio([]) == []


# ── Busca na web (server-side: quem executa e cobra é a API) ──

@pytest.mark.asyncio
async def test_busca_web_e_declarada_com_teto_e_vies_brasileiro(monkeypatch):
    """
    `user_location` não é detalhe: sem ele, buscar "Padaria Aurora" traz padaria
    em Portugal antes da do cliente.
    """
    cliente = _instalar(monkeypatch, [FakeResp([_texto("oi")])])

    await avancar(EstadoEntrevista(), None)

    busca = [t for t in cliente.chamadas[0]["tools"] if t["name"] == "web_search"][0]
    assert busca["type"] == "web_search_20260318"
    assert busca["max_uses"] == mi.MAX_USOS_BUSCA
    assert busca["user_location"]["country"] == "BR"


@pytest.mark.asyncio
async def test_buscas_web_sao_contadas_para_auditoria(monkeypatch):
    """
    A busca é cobrada por REQUEST, à parte dos tokens. Sem contar, o teto não
    existe e o gasto fica invisível.
    """
    _instalar(monkeypatch, [
        FakeResp([_texto("achei a empresa")], usage=_uso(buscas=2)),
        FakeResp([_texto("mais uma")], usage=_uso(buscas=1)),
    ])

    estado = await avancar(EstadoEntrevista(), "minha empresa e Padaria Aurora")
    estado = await avancar(estado, "confere")

    assert estado.buscas_web == 3


@pytest.mark.asyncio
async def test_resposta_sem_server_tool_use_nao_quebra_a_contagem(monkeypatch):
    _instalar(monkeypatch, [FakeResp([_texto("oi")], usage=_uso(buscas=None))])

    assert (await avancar(EstadoEntrevista(), None)).buscas_web == 0


@pytest.mark.asyncio
async def test_teto_de_buscas_web_encerra_a_entrevista(monkeypatch):
    """40 turnos × 3 usos seriam 120 buscas pagas por aba aberta de um anônimo."""
    _instalar(monkeypatch, [])

    with pytest.raises(EntrevistaIndisponivel):
        await avancar(EstadoEntrevista(buscas_web=mi.MAX_BUSCAS_WEB), "busca mais")


@pytest.mark.asyncio
async def test_turno_que_estoura_o_teto_de_busca_ainda_e_salvo(monkeypatch):
    """
    O teto é checado no INÍCIO de `avancar`, não no meio do loop. Levantar no meio
    perderia a conversa e o contador junto — e a busca seria refeita para sempre.
    """
    _instalar(monkeypatch, [FakeResp([_texto("achei")], usage=_uso(buscas=mi.MAX_BUSCAS_WEB))])

    estado = await avancar(EstadoEntrevista(), "acha minha empresa")

    assert estado.buscas_web == mi.MAX_BUSCAS_WEB
    assert ultima_fala(estado) == "achei", "a fala do turno que estourou foi perdida"
    assert EstadoEntrevista.from_json(estado.to_json()).buscas_web == mi.MAX_BUSCAS_WEB


@pytest.mark.asyncio
async def test_pause_turn_continua_o_turno_em_vez_de_encerrar(monkeypatch):
    """
    A busca server-side pode devolver o turno em pedaços com `pause_turn`.
    Tratar isso como fim entregaria uma resposta cortada no meio.
    """
    _instalar(monkeypatch, [
        FakeResp([_texto("procurando...")], stop_reason="pause_turn", usage=_uso(buscas=1)),
        FakeResp([_texto("Achei a Padaria Aurora na Vila Mariana. E a sua?")]),
    ])

    estado = await avancar(EstadoEntrevista(), "Padaria Aurora")

    assert "Vila Mariana" in ultima_fala(estado)
    assert estado.buscas_web == 1


@pytest.mark.asyncio
async def test_pause_turn_em_loop_nao_roda_para_sempre(monkeypatch):
    _instalar(monkeypatch, [
        FakeResp([_texto("...")], stop_reason="pause_turn") for _ in range(mi.MAX_TURNOS + 5)
    ])

    with pytest.raises(EntrevistaIndisponivel):
        await avancar(EstadoEntrevista(), "vai")


@pytest.mark.asyncio
async def test_pesquisa_nao_aparece_como_fala_na_tela(monkeypatch):
    monkeypatch.setattr(mi, "ler_site",
                        lambda u: _async({"url_final": u, "texto": "Paes", "truncado": False}),
                        raising=True)
    _instalar(monkeypatch, [
        FakeResp([_texto("Manda o site que eu dou uma olhada.")]),
        FakeResp([_texto("Deixa eu ver aqui."),
                  _tool("t1", "ler_site", {"url": "aurora.com"})], stop_reason="tool_use"),
        FakeResp([_texto("Voces fazem paes. Quem e o cliente tipico?")]),
    ])

    estado = await avancar(EstadoEntrevista(), None)
    estado = await avancar(estado, "aurora.com")

    visivel = historico_visivel(estado)
    assert [m["de"] for m in visivel] == ["mestre", "usuario", "mestre", "mestre"]
    assert all("CONTEÚDO DA PÁGINA" not in m["texto"] for m in visivel), \
        "o texto do site vazou para a tela como se fosse fala da Mestre"


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
