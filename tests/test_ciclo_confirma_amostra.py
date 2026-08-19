"""
O ciclo de treino não pode gritar lobo.

Uma amostra por caso não distingue "a mudança quebrou isso" de "o modelo variou".
Medido em produção em 2026-08-19: o caso `nao_inventa_dado` acerta ~1 em 6 rodando
o MESMO prompt, e num ciclo real ele apareceu como `QUEBROU` — regressão fantasma,
veredito `revisar`, num candidato que não havia quebrado nada.

Alarme falso é pior que nenhum alarme: o operador aprende a ignorar o veredito, e
aí o instrumento inteiro deixa de servir. Estes testes fixam o comportamento que
impede isso — reamostrar o que mudou e decidir por maioria.
"""

import asyncio
import itertools

import pytest

import services.mestre_ciclo as mc


class _Agente:
    location_id = "loc1"
    channel = "whatsapp"
    name = "Ellen"
    prompt = "prompt atual"
    qualification_enabled = False
    qualification_fields = []


def _sonda_falsa(monkeypatch, respostas):
    """
    `respostas[(caso_id, lado)]` é um iterável de bools consumido em ordem.
    lado: "antes" quando o prompt é None (o que está no agente), "depois" senão.
    """
    chamadas = []

    async def _rodar(agente, caso, prompt=None):
        lado = "antes" if prompt is None else "depois"
        chamadas.append((caso["id"], lado))
        ok = next(respostas[(caso["id"], lado)])
        return {"id": caso["id"], "ok": ok, "criterio": "c", "falhou_em": [] if ok else ["c"],
                "chamou": "-", "texto": "t", "texto_completo": "t"}

    monkeypatch.setattr(mc, "rodar_caso", _rodar, raising=False)
    import services.sonda as sonda
    monkeypatch.setattr(sonda, "rodar_caso", _rodar, raising=True)
    return chamadas


@pytest.fixture(autouse=True)
def sem_roteiro_de_disco(monkeypatch):
    """Isola do arquivo real: aqui se testa o motor de decisão, não o roteiro."""
    import services.sonda as sonda
    monkeypatch.setattr(sonda, "carregar_roteiros", lambda _c: [], raising=True)


def _rodar(casos, respostas, monkeypatch):
    _sonda_falsa(monkeypatch, respostas)
    return asyncio.run(mc.verificar(_Agente(), "prompt novo", casos))


CASO = [{"id": "flaky", "origem": "regressao", "lead": "x", "espera_tool": "t"}]
PEDIDO = {"id": "pedido", "origem": "pedido_do_operador", "lead": "y", "espera_texto": "z"}


def test_caso_INSTAVEL_nao_vira_regressao_fantasma(monkeypatch):
    """
    O cenário real: o caso acerta ~1/3 dos dois lados. A primeira amostra deu ok
    antes e FALHA depois — que com uma amostra só vira `QUEBROU`.
    Com confirmação, os dois lados empatam em 1/3 e o veredito é 'segue falhando'.
    """
    r = _rodar(
        CASO + [PEDIDO],
        {
            ("flaky", "antes"):  iter([True, False, False]),
            ("flaky", "depois"): iter([False, True, False]),
            ("pedido", "antes"): iter([False]),
            ("pedido", "depois"): iter([True] * 3),
        },
        monkeypatch,
    )
    flaky = next(l for l in r["linhas"] if l["id"] == "flaky")
    assert flaky["veredito"] != "QUEBROU", (
        "acusou regressão num caso que falha igual dos dois lados — "
        "é assim que o operador aprende a ignorar o alarme"
    )
    assert r["quebrou"] == []
    assert r["recomendacao"] == "publicar"


def test_regressao_DE_VERDADE_continua_sendo_acusada(monkeypatch):
    """
    O contrapeso: se o candidato realmente quebra, a maioria tem que mostrar.

    Os números são 3/3 → 1/3 de propósito. É a regressão realista — o candidato
    não zerou o caso, tornou-o raro. Uma regra do tipo "acertou pelo menos uma
    vez, então está ok" deixaria isso passar; a maioria não deixa.
    """
    r = _rodar(
        CASO + [PEDIDO],
        {
            ("flaky", "antes"):  iter([True, True, True]),
            ("flaky", "depois"): iter([False, True, False]),
            ("pedido", "antes"): iter([False]),
            ("pedido", "depois"): iter([True] * 3),
        },
        monkeypatch,
    )
    flaky = next(l for l in r["linhas"] if l["id"] == "flaky")
    assert flaky["veredito"] == "QUEBROU", (
        "3/3 antes e 1/3 depois é regressão; passou porque a decisão não é por maioria"
    )
    assert r["quebrou"] == ["flaky"]
    assert r["recomendacao"] == "revisar", "atender o pedido quebrando outra coisa não é sucesso"


def test_melhora_PARCIAL_nao_e_o_mesmo_que_conserto(monkeypatch):
    """
    Simétrico do anterior. Um caso que já acertava de vez em quando (1/3) e passou
    a acertar sempre (3/3) foi CORRIGIDO. Se a decisão fosse "acertou alguma vez",
    o antes já contaria como ok e o conserto sumiria do relatório — o operador não
    veria o ganho que pagou para ter.
    """
    r = _rodar(
        CASO + [PEDIDO],
        {
            ("flaky", "antes"):  iter([False, True, False]),
            ("flaky", "depois"): iter([True, True, True]),
            ("pedido", "antes"): iter([True]),
            ("pedido", "depois"): iter([True]),
        },
        monkeypatch,
    )
    flaky = next(l for l in r["linhas"] if l["id"] == "flaky")
    assert flaky["veredito"] == "corrigiu"
    assert flaky["amostras"] == "3/3 depois, 1/3 antes"


def test_passar_a_acertar_NA_MAIORIA_ja_conta_como_conserto(monkeypatch):
    """
    Por que maioria e não "acertou todas".

    Exigir 3/3 para considerar um lado bom soa mais rigoroso, mas apaga o ganho
    real: um caso que ia de 0/3 para 2/3 ficaria "segue falhando" dos dois lados e
    sumiria do relatório. Com 3 amostras, 2/3 não é ruído — é o agente passando a
    acertar na maior parte das vezes, que é o que o operador compra.
    """
    r = _rodar(
        CASO + [PEDIDO],
        {
            ("flaky", "antes"):  iter([False, False, False]),
            ("flaky", "depois"): iter([True, False, True]),
            ("pedido", "antes"): iter([True]),
            ("pedido", "depois"): iter([True]),
        },
        monkeypatch,
    )
    flaky = next(l for l in r["linhas"] if l["id"] == "flaky")
    assert flaky["veredito"] == "corrigiu", (
        "0/3 → 2/3 é melhora e sumiu do relatório: a decisão virou 'tem que acertar todas'"
    )
    assert flaky["amostras"] == "2/3 depois, 0/3 antes"


def test_so_o_que_MUDOU_e_reamostrado(monkeypatch):
    """
    Reamostrar tudo triplicaria a conta da Mestre. O que ficou igual dos dois lados
    não tem o que confirmar.
    """
    estavel = {"id": "estavel", "origem": "regressao", "lead": "x", "espera_tool": "t"}
    chamadas = _sonda_falsa(monkeypatch, {
        ("estavel", "antes"): itertools.repeat(True),
        ("estavel", "depois"): itertools.repeat(True),
        ("flaky", "antes"): iter([True, False, False]),
        ("flaky", "depois"): iter([False, False, True]),
        ("pedido", "antes"): itertools.repeat(False),
        ("pedido", "depois"): itertools.repeat(True),
    })
    asyncio.run(mc.verificar(_Agente(), "prompt novo", CASO + [estavel, PEDIDO]))

    por_caso = {}
    for cid, _lado in chamadas:
        por_caso[cid] = por_caso.get(cid, 0) + 1
    assert por_caso["estavel"] == 2, "reamostrou um caso que não mudou"
    assert por_caso["flaky"] == 2 * mc.CONFIRMACOES
    assert por_caso["pedido"] == 2 * mc.CONFIRMACOES, "o pedido mudou; tem que confirmar também"


def test_a_linha_diz_quantas_amostras_sustentam_o_veredito(monkeypatch):
    """
    "QUEBROU" de 1 amostra e "QUEBROU" de 3 não têm o mesmo peso na realidade;
    sem esta coluna, têm o mesmo peso na tela.
    """
    r = _rodar(
        CASO + [PEDIDO],
        {
            ("flaky", "antes"):  iter([True, True, True]),
            ("flaky", "depois"): iter([False, False, False]),
            ("pedido", "antes"): iter([True]),
            ("pedido", "depois"): iter([True]),
        },
        monkeypatch,
    )
    flaky = next(l for l in r["linhas"] if l["id"] == "flaky")
    pedido = next(l for l in r["linhas"] if l["id"] == "pedido")
    assert flaky["amostras"] == "0/3 depois, 3/3 antes"
    assert pedido["amostras"] == "1/1", "caso que não mudou não gasta amostra extra"


def test_erro_na_reamostra_nao_derruba_o_ciclo(monkeypatch):
    """A Mestre já foi paga; uma chamada que falha não pode perder o trabalho."""
    import services.sonda as sonda

    n = itertools.count()

    async def _rodar_caso(agente, caso, prompt=None):
        i = next(n)
        if i >= 2:                      # as reamostras explodem
            raise RuntimeError("API fora do ar")
        return {"id": caso["id"], "ok": prompt is None, "criterio": "c",
                "falhou_em": [], "chamou": "-", "texto": "t", "texto_completo": "t"}

    monkeypatch.setattr(mc, "rodar_caso", _rodar_caso, raising=False)
    monkeypatch.setattr(sonda, "rodar_caso", _rodar_caso, raising=True)

    r = asyncio.run(mc.verificar(_Agente(), "prompt novo", CASO))
    assert r["linhas"], "o ciclo morreu por causa de uma reamostra"
