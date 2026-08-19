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

from services.sonda import ROTEIRO_PADRAO as ROTEIRO


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
        criterios = ("espera_tool", "nao_espera_tool", "espera_texto", "nao_espera_texto")
        assert any(c.get(k) for k in criterios), \
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


# ── O critério que faltava: o agente RESPONDEU o lead? ──
#
# Em 2026-08-19 o roteiro media só qual ferramenta o agente chamou. A Mestre
# atendeu um pedido de abertura apagando a regra que mandava responder, o agente
# passou a devolver "em que posso te ajudar?" para quem já tinha perguntado, e
# nada aqui reprovou. O ciclo de treino leu "nada quebrou" e mandou publicar.

def test_o_roteiro_mede_se_o_agente_RESPONDE_e_nao_so_qual_tool_chama():
    casos = carregar()
    com_texto = [c for c in casos if c.get("espera_texto") or c.get("nao_espera_texto")]
    assert len(com_texto) >= 3, (
        "sem critério de TEXTO o roteiro não vê o primeiro turno do SDR, "
        "que é justamente onde o texto é o produto"
    )


def test_devolver_a_pergunta_ao_lead_esta_coberto_NOS_DOIS_SENTIDOS():
    """
    Só o caso negativo premiaria um agente que nunca se apresenta; só o positivo
    premiaria o que sempre recita a abertura e nunca responde. O valor está no par.
    """
    ids = [c["id"] for c in carregar()]
    assert "responde_quem_ja_declarou_intencao" in ids, "falta o caso que pega a regressão"
    assert "abertura_convida_o_lead_a_falar" in ids, "falta o contrapeso que protege o pedido do operador"


def test_a_regressao_de_2026_08_19_seria_reprovada_pelo_roteiro_de_hoje():
    """
    Regressão com dado REAL: os dois textos abaixo foram capturados da API em
    2026-08-19, para o MESMO lead ("queria entender o que vocês fazem"). O de cima
    é o prompt de antes (respondeu); o de baixo é o que a Mestre aplicou (devolveu
    a pergunta). Se o roteiro não separar os dois, ele não serve para nada.
    """
    from services.sonda import avaliar

    caso = next(c for c in carregar() if c["id"] == "responde_quem_ja_declarou_intencao")

    respondeu = (
        "Oi! A Eiai trabalha com automação de operações B2B — desde análise do processo "
        "até a implementação da solução.\n\nVocê já identificou algum gargalo na sua "
        "operação, ou ainda está explorando possibilidades?"
    )
    devolveu = "Oi! Tudo bem? Sou a Ellen, da Eiai Solutions.\n\nEm que posso te ajudar?"

    ok_bom, _, _ = avaliar(caso, [], respondeu)
    ok_ruim, _, falhas = avaliar(caso, [], devolveu)

    assert ok_bom, "reprovou a resposta que ATENDEU o lead"
    assert not ok_ruim, "aprovou a resposta que devolveu a pergunta ao lead"
    assert falhas, "reprovou sem dizer qual critério falhou"


def test_no_oi_seco_convidar_o_lead_a_falar_continua_CERTO():
    """O contrapeso: o mesmo texto que reprova acima tem que passar aqui."""
    from services.sonda import avaliar

    caso = next(c for c in carregar() if c["id"] == "abertura_convida_o_lead_a_falar")
    ok, _, falhas = avaliar(caso, [], "Oi! Tudo bem? Sou a Ellen, da Eiai Solutions.\n\nEm que posso te ajudar?")
    assert ok, f"reprovou a abertura que o operador pediu: {falhas}"


def test_o_criterio_reprova_TERMINAR_devolvendo_a_pergunta_e_nao_a_frase_em_si():
    """
    A âncora `$` do regex é a peça que faz o critério ser útil em vez de burro.

    Sem ela, "posso te ajudar" em QUALQUER posição reprova — e aí a resposta boa,
    que explica o serviço e no meio se oferece para ajudar, seria marcada como
    regressão. O critério tem que separar "ofereceu ajuda enquanto respondia" de
    "não respondeu nada e devolveu a pergunta".
    """
    from services.sonda import avaliar

    caso = next(c for c in carregar() if c["id"] == "responde_quem_ja_declarou_intencao")

    # A frase proibida aparece NO MEIO, e depois o agente responde de verdade.
    # Com a âncora: passa. Sem a âncora: reprova — e o critério vira ruído.
    ofereceu_ajuda_MAS_respondeu = (
        "Oi! Sou a Ellen. Já te conto em que posso te ajudar.\n\n"
        "A gente trabalha com automação de operações B2B: análise do processo, "
        "redesenho e implementação.\n\n"
        "Você já tem algum gargalo específico em mente?"
    )
    ok, _, falhas = avaliar(caso, [], ofereceu_ajuda_MAS_respondeu)
    assert ok, (
        "reprovou uma resposta que RESPONDEU o lead só porque a frase 'posso te "
        f"ajudar' aparece nela — o critério perdeu a âncora do fim. falhas={falhas}"
    )

    # E o contraexemplo: a mesma frase, mas como ÚLTIMA coisa dita.
    ok, _, _ = avaliar(caso, [], "Oi! Sou a Ellen.\n\nEm que posso te ajudar?")
    assert not ok


# ── O motor de avaliação ──

def test_TODOS_os_criterios_do_caso_valem_e_nao_so_o_primeiro():
    """
    Era `if/elif`: o caso afirmava só o primeiro critério e os demais eram lidos do
    JSON e ignorados em silêncio. Um caso que pedia tool E texto media só a tool.
    """
    from services.sonda import avaliar

    caso = {"id": "x", "nao_espera_tool": ESCALATE, "nao_espera_texto": r"proibido"}

    ok, _, falhas = avaliar(caso, [], "resposta limpa")
    assert ok

    # Tool certa, texto errado: com if/elif isto passaria.
    ok, _, falhas = avaliar(caso, [], "isto é proibido")
    assert not ok, "ignorou o segundo critério do caso"
    assert falhas == ["texto NÃO casa /proibido/"]

    # Texto certo, tool errada.
    ok, _, falhas = avaliar(caso, [ESCALATE], "resposta limpa")
    assert not ok
    assert falhas == [f"NÃO pode chamar {ESCALATE}"]

    # Os dois errados: reporta os dois, não só o primeiro.
    ok, _, falhas = avaliar(caso, [ESCALATE], "isto é proibido")
    assert not ok and len(falhas) == 2


def test_caso_sem_criterio_nao_reprova_ninguem():
    from services.sonda import avaliar

    ok, criterio, falhas = avaliar({"id": "x"}, [], "qualquer coisa")
    assert ok and falhas == [] and "sem critério" in criterio


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
