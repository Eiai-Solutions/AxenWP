"""
Três defeitos que faziam o agente SDK trabalhar contra o próprio dono.

Nenhum deles é "prompt fraco". São instruções e mecanismos do PRÓPRIO produto
empurrando o agente para o comportamento errado — e os três são silenciosos.

1. O gerador MANDAVA o agente transferir quando o lead estava pronto pra fechar.
   No motor legado era texto morto; no SDK, `escalar` PAUSA a conversa. O agente
   se calava no instante da venda, e todo prompt novo nascia com essa linha.

2. O dispatch de `qualificar` devolvia "ok" INCONDICIONALMENTE. O guard de
   completude rodava depois do turno e descartava em silêncio: o modelo ouvia
   "registrado", se despedia prometendo contato — e o CRM ficava vazio.

3. O `input_schema` da tool usava só o `label` do campo, jogando fora a
   `description` que a Mestre escreveu sobre como reconhecer aquele dado.
"""

import pytest

from services.agent_engine.tools import _qualify_spec


# ── 1. O gerador não pode mandar escalar no fechamento ──

def test_o_gerador_nao_manda_transferir_lead_pronto_pra_fechar():
    import re

    from utils.master_prompt import MASTER_SYSTEM_PROMPT

    # Normaliza espaço: o prompt é quebrado em linhas e a frase atravessa a quebra.
    texto = re.sub(r"\s+", " ", MASTER_SYSTEM_PROMPT.lower())

    # A seção de ESCALAÇÃO não pode LISTAR o fechamento como gatilho. A proibição
    # ("nunca liste ... pronto pra fechar") é o oposto disso e precisa sobreviver,
    # então a checagem é sobre a frase de LISTA, não sobre a palavra solta.
    secao = texto.split("## escalação", 1)[-1].split("═══", 1)[0]
    lista = secao.split("nunca liste", 1)[0]
    assert "pronto pra fechar" not in lista, \
        "o gerador voltou a listar 'pronto pra fechar' como motivo de transferência"
    assert "lead qualificado" not in lista, \
        "o gerador voltou a listar 'lead qualificado' como motivo de transferência"

    # A regra tem que estar ESCRITA, não só o gatilho removido: sem o porquê, o
    # modelo reinventa a linha por conta própria no próximo prompt que gerar.
    assert "nunca liste" in secao, "falta a proibição explícita"
    assert "pausa a conversa" in secao, "falta explicar POR QUE não se transfere no fechamento"


# ── 2. O dispatch tem que dizer a verdade ──

class _AgenteFalso:
    """Só o suficiente para exercitar o dispatch real."""

    qualification_fields = [
        {"key": "nome", "label": "Nome completo"},
        {"key": "orcamento", "label": "Orçamento"},
        {"key": "origem", "label": "Origem", "auto": True},
    ]

    from services.ai_service import AIEngine
    _qualification_complete = AIEngine._qualification_complete
    _claude_tool_dispatch = AIEngine._claude_tool_dispatch


@pytest.mark.asyncio
async def test_qualificar_incompleto_devolve_a_verdade_ao_modelo():
    """
    REGRESSÃO — o lead era avisado de que alguém entraria em contato, e ninguém
    era registrado. O silêncio era total: a conversa salva parecia um fechamento.
    """
    r = await _AgenteFalso()._claude_tool_dispatch("register_qualified_lead", {"nome": "Luiz"}, None)

    assert r["status"] == "incompleto", "continuou dizendo 'ok' com dados faltando"
    assert "Orçamento" in r["faltam"]
    assert "Nome completo" not in r["faltam"], "cobrou um campo que o lead já deu"
    assert "não se despeça" in r["instrucao"].lower()


@pytest.mark.asyncio
async def test_qualificar_completo_confirma():
    r = await _AgenteFalso()._claude_tool_dispatch(
        "register_qualified_lead", {"nome": "Luiz", "orcamento": "10 mil"}, None
    )

    assert r["status"] == "ok"


@pytest.mark.asyncio
async def test_campo_em_branco_conta_como_faltando():
    """String vazia e espaço em branco não são coleta."""
    r = await _AgenteFalso()._claude_tool_dispatch(
        "register_qualified_lead", {"nome": "Luiz", "orcamento": "   "}, None
    )

    assert r["status"] == "incompleto" and "Orçamento" in r["faltam"]


@pytest.mark.asyncio
async def test_campo_auto_nao_e_cobrado_do_modelo():
    """`auto` é preenchido pelo sistema; cobrar do modelo o faria inventar."""
    r = await _AgenteFalso()._claude_tool_dispatch(
        "register_qualified_lead", {"nome": "Luiz", "orcamento": "10 mil"}, None
    )

    assert r["status"] == "ok", "exigiu um campo que o sistema preenche sozinho"


# ── 3. A description do campo chega ao modelo ──

def test_o_schema_da_tool_usa_a_description_que_a_mestre_escreveu():
    spec = _qualify_spec([
        {"key": "orcamento", "label": "Orçamento",
         "description": "Faixa de investimento que o lead menciona, mesmo aproximada"},
    ])

    d = spec.input_schema["properties"]["orcamento"]["description"]
    assert d.startswith("Faixa de investimento"), "voltou a usar só o label"


def test_sem_description_cai_no_label():
    spec = _qualify_spec([{"key": "orcamento", "label": "Orçamento"}])

    assert spec.input_schema["properties"]["orcamento"]["description"] == "Orçamento"
