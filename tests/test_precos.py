"""
O que impede o painel de voltar a mentir sobre dinheiro.

O defeito original não foi um cálculo errado: foi a AUSÊNCIA de cálculo somada a
um default de `0.0`, que produz a pior saída possível — um número que parece
medido. Os testes aqui protegem as três fronteiras onde isso pode voltar:

  · zero vs desconhecido   — modelo fora da tabela precisa devolver None
  · cache é preço próprio  — 10% na leitura, 125% na escrita, não 100%
  · custo fora dos tokens  — busca web é cobrada por requisição
"""

from datetime import date

import pytest

from services import precos
from services.precos import custo


# ── zero e desconhecido são coisas diferentes ──────────────────────────────

def test_modelo_fora_da_tabela_devolve_None_e_nao_zero():
    """
    `0.0` some na soma e vira economia imaginária; `None` sobe até a tela como
    "não precificado". É a diferença entre a conta certa e a conta bonita.
    """
    assert custo("anthropic", "claude-modelo-que-nao-existe", input_tokens=1_000_000) is None


def test_servico_sem_tabela_devolve_None():
    assert custo("elevenlabs", model=None, characters=50_000) is None
    assert custo("openrouter", "openai/gpt-4o", input_tokens=1000) is None


# ── TTS do Fish: as duas engines têm preços muito diferentes ───────────────

def test_s2pro_e_precificado_pela_medicao_na_conta():
    """4.000 caracteres derrubaram o crédito em exatamente $0,12 → $30/1M."""
    assert custo("fishaudio", "s2-pro", characters=4_000) == pytest.approx(0.12)
    assert custo("fishaudio", "s2-pro", characters=1_000_000) == pytest.approx(30.0)


def test_s1_fica_SEM_PRECO_e_nao_como_gratuito():
    """
    8.000 caracteres não moveram o crédito nesta conta — mas plano diferente cobra
    diferente. Gravar 0.0 diria "de graça" para todo tenant; `None` diz "não sei",
    que é a verdade e é o que a tela mostra em ocre.
    """
    assert custo("fishaudio", "s1", characters=1_000_000) is None


def test_as_duas_engines_do_fish_nao_somam_no_mesmo_balde():
    """
    Sem a engine na linha de uso, s1 e s2-pro viram um numero so. A diferenca
    medida e grande demais para ficar invisivel: uma nao consumiu credito, a outra
    custa $30/1M.
    """
    assert custo("fishaudio", "s2-pro", characters=10_000) is not None
    assert custo("fishaudio", "s1", characters=10_000) is None
    assert custo("fishaudio", None, characters=10_000) is None, \
        "linha sem engine nao pode herdar o preco da cara"


def test_chamada_de_verdade_nunca_custa_zero():
    """Guarda direta contra o sintoma que o Luiz viu: tokens > 0 e custo 0."""
    v = custo("anthropic", "claude-opus-5", input_tokens=1000, output_tokens=500)
    assert v is not None and v > 0


# ── cache: o grosso da conta ───────────────────────────────────────────────

def test_leitura_de_cache_custa_um_decimo_da_entrada():
    cheia = custo("anthropic", "claude-opus-5", input_tokens=1_000_000)
    cache = custo("anthropic", "claude-opus-5", cache_read_tokens=1_000_000)
    assert cheia == pytest.approx(5.0)
    assert cache == pytest.approx(0.5)


def test_escrita_de_cache_custa_MAIS_que_a_entrada():
    """
    Sinal invertido de propósito: quem tratar escrita como desconto vai quebrar
    aqui. Gravar o prefixo custa 125% — é o turno mais caro da conversa.
    """
    cheia = custo("anthropic", "claude-opus-5", input_tokens=1_000_000)
    escrita = custo("anthropic", "claude-opus-5", cache_write_tokens=1_000_000)
    assert escrita > cheia
    assert escrita == pytest.approx(6.25)


def test_ignorar_o_cache_erraria_a_conta_em_ordem_de_grandeza():
    """
    O cenário real: prefixo de 20k cacheado, 200 tokens novos, 300 de saída.
    Se alguém voltar a somar cache dentro de input_tokens, o valor salta ~10x —
    e este teste é o que avisa.
    """
    certo = custo(
        "anthropic", "claude-opus-5",
        input_tokens=200, output_tokens=300, cache_read_tokens=20_000,
    )
    como_se_fosse_entrada = custo(
        "anthropic", "claude-opus-5", input_tokens=20_200, output_tokens=300,
    )
    assert como_se_fosse_entrada > certo * 5


def test_sem_cache_a_conta_e_so_entrada_e_saida():
    v = custo("anthropic", "claude-opus-5", input_tokens=1_000_000, output_tokens=1_000_000)
    assert v == pytest.approx(30.0)


# ── custo que não é token ──────────────────────────────────────────────────

def test_busca_web_e_cobrada_por_requisicao():
    v = custo("anthropic", "claude-opus-5", buscas_web=1000)
    assert v == pytest.approx(10.0)


def test_busca_web_conta_mesmo_com_modelo_desconhecido():
    """
    O preço da busca é da ferramenta, não do modelo. Devolver None aqui apagaria
    um gasto que a Anthropic cobrou de qualquer jeito.
    """
    v = custo("anthropic", "modelo-nao-tabelado", buscas_web=100)
    assert v == pytest.approx(1.0)


# ── a promoção vence sozinha ───────────────────────────────────────────────

def test_sonnet5_usa_o_preco_promocional_ate_31_08_e_o_cheio_depois():
    """
    Sonnet 5 é o default de TODO caminho do projeto. Fixar $3/$15 hoje
    superestimaria 50%; fixar $2/$10 subestimaria em setembro. A regra tem data.
    """
    durante = custo("anthropic", "claude-sonnet-5", input_tokens=1_000_000,
                    quando=date(2026, 8, 19))
    depois = custo("anthropic", "claude-sonnet-5", input_tokens=1_000_000,
                   quando=date(2026, 9, 1))
    assert durante == pytest.approx(2.0)
    assert depois == pytest.approx(3.0)
    assert precos.PRECOS_ANTHROPIC["claude-sonnet-5"] == (3.0, 15.0)


def test_sufixo_de_data_no_id_do_modelo_nao_derruba_o_preco():
    """`claude-haiku-4-5-20251001` é o mesmo preço de `claude-haiku-4-5`."""
    assert custo("anthropic", "claude-haiku-4-5-20251001", input_tokens=1_000_000) \
        == custo("anthropic", "claude-haiku-4-5", input_tokens=1_000_000)


# ── tabela de fora, para provedor não tabelado ─────────────────────────────

def test_precos_extra_do_ambiente_precifica_o_que_o_codigo_nao_sabe(monkeypatch):
    monkeypatch.setenv(
        "PRECOS_EXTRA_JSON",
        '{"elevenlabs:*": {"caractere": 300}, "openrouter:openai/gpt-4o": {"entrada": 2.5, "saida": 10}}',
    )
    assert custo("elevenlabs", "eleven_turbo_v2", characters=1_000_000) == pytest.approx(300.0)
    assert custo("openrouter", "openai/gpt-4o", input_tokens=1_000_000) == pytest.approx(2.5)


def test_precos_extra_ilegivel_nao_derruba_nada(monkeypatch):
    monkeypatch.setenv("PRECOS_EXTRA_JSON", "{isto não é json")
    assert custo("anthropic", "claude-opus-5", input_tokens=1_000_000) == pytest.approx(5.0)


# ── leitura do `usage` da API ──────────────────────────────────────────────

def test_extrair_uso_le_a_busca_web_de_dentro_do_server_tool_use():
    """
    A contagem de busca mora aninhada em `usage.server_tool_use`. Três dos quatro
    caminhos da Mestre esqueciam de olhar lá — daí a leitura ser uma função só.
    """
    from services.usage_logger import extrair_uso

    class _Servidor:
        web_search_requests = 3

    class _Uso:
        input_tokens = 10
        output_tokens = 20
        cache_read_input_tokens = 30
        cache_creation_input_tokens = 40
        server_tool_use = _Servidor()

    d = extrair_uso(_Uso())
    assert d == {
        "input_tokens": 10, "output_tokens": 20,
        "cache_read_tokens": 30, "cache_write_tokens": 40,
        "buscas_web": 3,
    }


def test_extrair_uso_aceita_dict_sem_server_tool_use():
    """O caminho do agente devolve `usage` como dict puro, sem busca web."""
    from services.usage_logger import extrair_uso

    d = extrair_uso({"input_tokens": 1, "output_tokens": 2, "cache_read_input_tokens": 3})
    assert d["buscas_web"] == 0
    assert d["cache_read_tokens"] == 3
    assert d["cache_write_tokens"] == 0


def test_extrair_uso_vazio_nao_gera_registro():
    from services.usage_logger import extrair_uso

    assert extrair_uso(None) == {}
    assert extrair_uso({}) == {}


# ── o circuito fechado: o que de fato vai para o banco ─────────────────────

@pytest.fixture
def banco(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from data.models import Base, Tenant

    engine = create_engine(f"sqlite:///{tmp_path}/uso.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    db = Session()
    db.add(Tenant(location_id="loc1", company_name="Eiai"))
    db.commit()
    db.close()

    import services.usage_logger as ul
    monkeypatch.setattr(ul, "SessionLocal", Session, raising=True)
    return Session


def _linhas(Session):
    from data.models import UsageLog

    db = Session()
    try:
        return db.query(UsageLog).order_by(UsageLog.id).all()
    finally:
        db.close()


def test_o_gravador_calcula_sozinho_e_a_linha_nasce_com_custo(banco):
    """
    O defeito literal: `save_usage_log` tinha `cost_usd: float = 0.0` e NENHUM
    chamador passava valor, então toda linha nascia zerada. Este teste é o que
    trava o default de volta em `None` (= calcule).
    """
    from services.usage_logger import save_usage_log

    save_usage_log(
        location_id="loc1", service="anthropic", model="claude-opus-5",
        input_tokens=2304, output_tokens=346,
    )
    (linha,) = _linhas(banco)
    assert linha.cost_usd is not None and linha.cost_usd > 0
    assert linha.cost_usd == pytest.approx(2304 * 5.0 / 1e6 + 346 * 25.0 / 1e6)


def test_a_linha_guarda_os_tokens_de_cache_que_antes_eram_descartados(banco):
    from services.usage_logger import save_usage_log

    save_usage_log(
        location_id="loc1", service="anthropic", model="claude-opus-5",
        input_tokens=200, output_tokens=300,
        cache_read_tokens=20_000, cache_write_tokens=6_000,
    )
    (linha,) = _linhas(banco)
    assert linha.cache_read_tokens == 20_000
    assert linha.cache_write_tokens == 6_000
    esperado = (200 * 5 + 20_000 * 0.5 + 6_000 * 6.25 + 300 * 25) / 1e6
    assert linha.cost_usd == pytest.approx(esperado)


def test_modelo_sem_preco_grava_NULL_e_nao_zero(banco):
    """NULL sobe para o painel como "não precificado"; zero viraria economia."""
    from services.usage_logger import save_usage_log

    save_usage_log(location_id="loc1", service="groq", model="whisper-large-v3")
    (linha,) = _linhas(banco)
    assert linha.cost_usd is None


def test_zero_explicito_continua_significando_de_graca(banco):
    """`None` = calcule; `0.0` = medi e foi zero. Um não pode virar o outro."""
    from services.usage_logger import save_usage_log

    save_usage_log(location_id="loc1", service="anthropic", model="claude-opus-5",
                   input_tokens=1000, cost_usd=0.0)
    (linha,) = _linhas(banco)
    assert linha.cost_usd == 0.0


def test_a_mestre_e_gravada_numa_origem_propria(banco):
    """
    A pergunta do dono era "quanto custa o atendimento E quanto custa a Mestre".
    Sem esta coluna as duas contas viram uma só e a pergunta não tem resposta.
    """
    import asyncio

    from services.usage_logger import registrar_gasto_mestre, save_usage_log

    save_usage_log(location_id="loc1", service="anthropic", model="claude-opus-5",
                   input_tokens=100, output_tokens=50)

    class _Uso:
        input_tokens = 400
        output_tokens = 900
        cache_read_input_tokens = 0
        cache_creation_input_tokens = 0
        server_tool_use = None

    asyncio.run(registrar_gasto_mestre("loc1", "claude-opus-5", _Uso()))

    atendimento, mestre = _linhas(banco)
    assert atendimento.origem == "atendimento"
    assert mestre.origem == "mestre"
    assert mestre.cost_usd > atendimento.cost_usd


def test_gasto_da_mestre_sem_tenant_nao_explode_e_nao_grava(banco):
    """
    A FK exige `tenants.location_id`. Inventar um dono para não perder o dado
    seria pior: o custo apareceria no painel de outra pessoa.
    """
    import asyncio

    from services.usage_logger import registrar_gasto_mestre

    asyncio.run(registrar_gasto_mestre(None, "claude-opus-5", {"input_tokens": 10}))
    assert _linhas(banco) == []
