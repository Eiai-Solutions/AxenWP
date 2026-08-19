"""
O ENCANAMENTO dos roteiros de comportamento — custo zero, dentro do pytest.

Divisão com `scripts/sonda_agente.py`, que usa os MESMOS roteiros:

  aqui            → o contrato chega ao modelo? a política está no system prompt?
                    a tool certa é oferecida? o dispatch devolve o que deve?
  sonda (API real)→ dado tudo isso, o modelo JULGA certo?

O julgamento custa dinheiro e varia entre rodadas; o encanamento não pode variar.
Separar os dois é o que permite rodar um a cada commit e o outro quando se mexe
no prompt.
"""

import json
import pathlib

import pytest

from services.agent_engine.tools import ESCALATE, QUALIFY, build_tool_specs
from services.prompt_builder import build_system_prompt

ROTEIRO = pathlib.Path(__file__).parent / "roteiros" / "comportamento_sdr.json"


def carregar():
    return json.loads(ROTEIRO.read_text(encoding="utf-8"))["casos"]


class _Config:
    """Agente mínimo, no formato que `build_tool_specs` espera."""

    def __init__(self, enabled=True, campos=None):
        self.qualification_enabled = enabled
        self.qualification_fields = campos if campos is not None else [
            {"key": "orcamento", "label": "Orçamento", "description": "Faixa de investimento"},
        ]


# ── O roteiro em si é um artefato: se ele apodrecer, a medição mente ──

def test_todo_caso_tem_criterio_e_motivo():
    casos = carregar()
    assert casos, "roteiro vazio"
    for c in casos:
        assert c.get("id"), "caso sem id"
        assert c.get("lead"), f"{c['id']}: sem fala do lead"
        assert c.get("espera_tool") or c.get("nao_espera_tool"), \
            f"{c['id']}: sem critério — um caso que não afirma nada não mede nada"
        assert len(c.get("porque") or "") > 30, \
            f"{c['id']}: sem 'porque'. Cada caso existe para impedir um bug de voltar; " \
            "sem o motivo escrito, o próximo a ler não sabe se pode apagar."


def test_ids_sao_unicos():
    ids = [c["id"] for c in carregar()]
    assert len(ids) == len(set(ids))


def test_o_roteiro_cobre_os_DOIS_lados_da_escalacao():
    """
    Só casos de "deve escalar" premiariam um agente que escala sempre; só casos de
    "não deve" premiariam um que nunca escala. O valor está em ter os dois.
    """
    casos = carregar()
    deve = [c for c in casos if c.get("espera_tool") == ESCALATE]
    nao_deve = [c for c in casos if c.get("nao_espera_tool") == ESCALATE]

    assert len(deve) >= 2, "poucos casos de escalação legítima"
    assert len(nao_deve) >= 2, "poucos casos de escalação indevida"


def test_o_fechamento_esta_coberto():
    """O defeito de 2026-08-18 — escalar no fechamento — não pode sair do roteiro."""
    ids = [c["id"] for c in carregar()]
    assert "nao_escala_no_fechamento" in ids


# ── O contrato que chega ao modelo ──

def test_a_tool_de_qualificar_so_aparece_com_campo_de_COLETA():
    """
    O agente do Luiz tem 3 campos, os 3 `auto` (Nome/Email/Estado vêm do CRM).
    Sem campo de coleta a ferramenta não tem o que pedir, e oferecê-la seria dar
    ao modelo uma ação que nunca completa.
    """
    so_auto = _Config(campos=[{"key": "nome", "label": "Nome", "auto": True}])
    nomes = [t.name for t in build_tool_specs(so_auto)]
    assert nomes == [ESCALATE], "ofereceu qualificar sem ter o que coletar"

    com_coleta = _Config()
    assert QUALIFY in [t.name for t in build_tool_specs(com_coleta)]


def test_escalar_esta_sempre_disponivel():
    """Mesmo sem qualificação, pedir humano tem que funcionar."""
    assert ESCALATE in [t.name for t in build_tool_specs(_Config(enabled=False))]


def test_o_prompt_de_qualificacao_so_entra_quando_a_tool_entra():
    """
    Texto mandando chamar `register_qualified_lead` sem a tool no request é pedir
    para o modelo alucinar uma chamada — ou pior, escrever a chamada como texto.
    """
    so_auto = _Config(campos=[{"key": "nome", "label": "Nome", "auto": True}])
    p = build_system_prompt(
        "Você é a Ellen.",
        qualification_enabled=so_auto.qualification_enabled,
        qualification_fields=so_auto.qualification_fields,
        for_tools=True,
    )
    tools = [t.name for t in build_tool_specs(so_auto)]

    if QUALIFY not in tools:
        assert QUALIFY not in p, \
            "o prompt manda chamar uma ferramenta que não foi oferecida ao modelo"


def test_a_description_do_campo_chega_ao_schema():
    spec = next(t for t in build_tool_specs(_Config()) if t.name == QUALIFY)

    d = spec.input_schema["properties"]["orcamento"]["description"]
    assert d == "Faixa de investimento", "voltou a usar só o label"


@pytest.mark.parametrize("caso", carregar(), ids=lambda c: c["id"])
def test_cada_caso_e_executavel(caso):
    """O roteiro precisa montar um contexto válido — senão a sonda quebra na hora errada."""
    from services.agent_engine.base import AgentContext

    ctx = AgentContext(
        location_id="loc", session_id=f"sonda_{caso['id']}", user_phone="55",
        system_prompt="p", history=list(caso.get("historico") or []),
        incoming_text=caso["lead"], tools=build_tool_specs(_Config()),
    )
    assert ctx.incoming_text
    for m in ctx.history:
        assert m.get("role") in ("user", "assistant"), f"{caso['id']}: papel inválido no histórico"
        assert m.get("content")
