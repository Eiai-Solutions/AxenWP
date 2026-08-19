"""
Migration 036: as colunas que faltavam para o custo ser calculável.

Em dev o schema vem do `create_all` (o modelo já tem as colunas novas) e a
migration não tem o que fazer. Em PRODUÇÃO `usage_logs` existe desde a 008 e
`create_all` não altera tabela existente — a migration é a ÚNICA via. Foi assim
que a FK da 034 nunca chegou lá.

Por isso o banco deste teste é montado no schema ANTIGO, por SQL cru, e não pelo
`create_all`: rodar contra o schema novo provaria só que a migration não quebra
nada, não que ela faz o serviço.
"""

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

MIGRATION = Path(__file__).resolve().parents[1] / "alembic/versions/036_custo_real_e_origem.py"

# O schema exatamente como a 008 o deixou — sem cache, sem busca, sem origem.
SCHEMA_ANTIGO = """
CREATE TABLE usage_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id VARCHAR NOT NULL,
    service VARCHAR(50) NOT NULL,
    model VARCHAR(100),
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    characters INTEGER DEFAULT 0,
    cost_usd FLOAT DEFAULT 0.0,
    created_at DATETIME
)
"""


def _carrega():
    spec = importlib.util.spec_from_file_location("mig036", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def banco(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/u.db", connect_args={"check_same_thread": False})
    with engine.begin() as c:
        c.execute(text(SCHEMA_ANTIGO))
        c.execute(text(
            "INSERT INTO usage_logs (location_id, service, model, input_tokens, "
            "output_tokens, cost_usd, created_at) VALUES "
            "('loc1', 'anthropic', 'claude-sonnet-5', 2304, 346, 0.0, '2026-08-18 10:00:00')"
        ))
    return engine


def _rodar(engine):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    mod = _carrega()
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            mod.upgrade()


def _colunas(engine) -> set:
    return {c["name"] for c in inspect(engine).get_columns("usage_logs")}


def test_as_quatro_colunas_entram_numa_tabela_que_ja_existia(banco):
    assert "origem" not in _colunas(banco)
    _rodar(banco)
    assert {"cache_read_tokens", "cache_write_tokens", "buscas_web", "origem"} <= _colunas(banco)


def test_a_linha_antiga_e_marcada_como_atendimento_e_nao_fica_NULL(banco):
    """
    `server_default` (e não só o default do ORM) é o que preenche o passado. Com
    NULL ali, o agrupamento por origem no painel deixaria a linha de fora e o
    total por origem não bateria com o total geral.
    """
    _rodar(banco)
    with banco.begin() as c:
        origem, cache_lido, buscas = c.execute(text(
            "SELECT origem, cache_read_tokens, buscas_web FROM usage_logs"
        )).one()
    assert origem == "atendimento"
    assert cache_lido == 0
    assert buscas == 0


def test_o_custo_antigo_NAO_e_mexido_pela_migration(banco):
    """
    Calcular preço aqui exigiria a tabela de preços dentro da migration, e desde
    a 032 migration que falha derruba o boot. O backfill é script à parte."""
    _rodar(banco)
    with banco.begin() as c:
        assert c.execute(text("SELECT cost_usd FROM usage_logs")).scalar() == 0.0


def test_rodar_duas_vezes_nao_quebra(banco):
    """Ela roda no boot, em todo deploy."""
    _rodar(banco)
    _rodar(banco)
    assert len([c for c in inspect(banco).get_columns("usage_logs") if c["name"] == "origem"]) == 1


def test_banco_sem_a_tabela_nao_explode(tmp_path):
    """Instalação nova: `create_all` ainda não rodou nesta ordem em algum caminho."""
    engine = create_engine(f"sqlite:///{tmp_path}/vazio.db")
    _rodar(engine)  # não levanta
    assert "usage_logs" not in inspect(engine).get_table_names()


def test_o_backfill_precifica_a_linha_antiga(banco, monkeypatch):
    """
    O script existe porque a migration não faz isso. Se ele parar de funcionar, a
    linha de 2.650 tokens continua valendo $0,00 no painel para sempre.
    """
    from sqlalchemy.orm import sessionmaker

    _rodar(banco)
    Session = sessionmaker(bind=banco)

    import data.database as ddb
    monkeypatch.setattr(ddb, "SessionLocal", Session, raising=True)

    import importlib
    script = importlib.util.spec_from_file_location(
        "backfill", Path(__file__).resolve().parents[1] / "scripts/backfill_custos.py"
    )
    mod = importlib.util.module_from_spec(script)
    script.loader.exec_module(mod)
    monkeypatch.setattr(mod, "SessionLocal", Session, raising=True)
    monkeypatch.setattr("sys.argv", ["backfill", "--aplicar"])

    assert mod.main() == 0

    with banco.begin() as c:
        custo = c.execute(text("SELECT cost_usd FROM usage_logs")).scalar()
    # 2304 entrada + 346 saída no Sonnet 5 promocional ($2/$10 por 1M) em 18/08.
    assert custo == pytest.approx(2304 * 2.0 / 1e6 + 346 * 10.0 / 1e6)
