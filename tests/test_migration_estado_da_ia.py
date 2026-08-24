"""
Migration 037: o backfill é a parte que importa.

Sem ele, no instante do deploy, todo lead que estava pausado — por qualificação ou
por handoff — volta a ser atendido pelo robô, por cima de negociações que humanos
assumiram. É uma migration de dados disfarçada de migration de schema.

O banco aqui é montado no schema ANTIGO por SQL cru, e não pelo `create_all`: em
produção `qualified_leads` existe desde a 008 e `create_all` não altera tabela que
já existe, então a migration é a única via. Testar contra o schema novo provaria
só que ela não quebra nada — não que ela faz o serviço.
"""

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

MIGRATION = Path(__file__).resolve().parents[1] / "alembic/versions/037_estado_da_ia_por_conversa.py"

SCHEMA_ANTIGO = """
CREATE TABLE tenants (
    location_id VARCHAR PRIMARY KEY,
    company_name VARCHAR,
    mode VARCHAR DEFAULT 'ghl'
);
CREATE TABLE ai_agents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id VARCHAR NOT NULL,
    channel VARCHAR DEFAULT 'whatsapp',
    name VARCHAR, prompt TEXT, model VARCHAR,
    is_active BOOLEAN DEFAULT 0,
    qualification_enabled BOOLEAN DEFAULT 0
);
CREATE TABLE qualified_leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id VARCHAR NOT NULL,
    phone VARCHAR NOT NULL,
    ghl_opportunity_id VARCHAR,
    qualified_data JSON,
    summary TEXT,
    created_at DATETIME
);
"""


def _carrega():
    spec = importlib.util.spec_from_file_location("mig037", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def banco(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/e.db",
                           connect_args={"check_same_thread": False})
    with engine.begin() as c:
        for stmt in SCHEMA_ANTIGO.strip().split(";"):
            if stmt.strip():
                c.execute(text(stmt))
        # `whatsapp_only`: aqui o gate do pipeline pausava por qualified_lead
        # independentemente de a qualificação estar ligada.
        c.execute(text("INSERT INTO tenants VALUES ('loc1', 'Eiai', 'whatsapp_only')"))
        # Dois canais com agente: o estado de hoje pausa nos DOIS (o portão que
        # ficava no ai_service não olhava canal).
        c.execute(text("INSERT INTO ai_agents (location_id, channel, is_active) "
                       "VALUES ('loc1','whatsapp',1), ('loc1','telegram',1)"))
        c.execute(text(
            "INSERT INTO qualified_leads (location_id, phone, qualified_data, created_at) "
            "VALUES ('loc1','5547111','{\"orcamento\":\"10k\"}','2026-08-01 10:00:00'),"
            "       ('loc1','5547222','{\"_handoff\": true}','2026-08-02 11:00:00')"
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


def test_quem_estava_pausado_CONTINUA_pausado(banco):
    """O ponto inteiro da migration. Sem isto, o robô fala por cima do humano."""
    _rodar(banco)
    with banco.begin() as c:
        linhas = c.execute(text(
            "SELECT contact_ref, channel, enabled FROM conversation_ai_state "
            "ORDER BY contact_ref, channel"
        )).all()
    # 2 leads x 2 canais com agente
    assert len(linhas) == 4
    assert all(l.enabled in (0, False) for l in linhas), "alguém nasceu despausado"


def test_pausa_entra_nos_canais_QUE_TEM_AGENTE(banco):
    """
    O portão antigo (no `ai_service`) valia para todo canal, então o efeito de hoje
    é 'pausado em todo canal'. A chave passa a ter canal — mas quem já estava
    pausado continua pausado onde estava.
    """
    _rodar(banco)
    with banco.begin() as c:
        canais = {r.channel for r in c.execute(text(
            "SELECT DISTINCT channel FROM conversation_ai_state"))}
    assert canais == {"whatsapp", "telegram"}


def test_as_pausas_antigas_nascem_SEM_prazo(banco):
    """
    Elas nasceram eternas. Inventar vencimento retroativo religaria conversas em
    silêncio — o oposto do que esta migration existe para evitar.
    """
    _rodar(banco)
    with banco.begin() as c:
        assert c.execute(text(
            "SELECT count(*) FROM conversation_ai_state WHERE until IS NOT NULL")).scalar() == 0


def test_a_politica_nasce_preservando_o_comportamento(banco):
    """`pausar_ao_qualificar` default TRUE: este deploy não muda comportamento."""
    _rodar(banco)
    cols = {c["name"] for c in inspect(banco).get_columns("ai_agents")}
    assert "pausar_ao_qualificar" in cols
    with banco.begin() as c:
        vals = [r[0] for r in c.execute(text("SELECT pausar_ao_qualificar FROM ai_agents"))]
    assert all(v in (1, True) for v in vals)


def test_rodar_duas_vezes_nao_duplica(banco):
    """Ela roda no boot, em todo deploy."""
    _rodar(banco)
    _rodar(banco)
    with banco.begin() as c:
        assert c.execute(text("SELECT count(*) FROM conversation_ai_state")).scalar() == 4


def test_religar_a_mao_sobrevive_a_migration_rodar_de_novo(banco):
    """
    O caso que o `NOT EXISTS` protege: alguém religa uma conversa depois do
    primeiro deploy, e o próximo deploy roda a migration outra vez. Religar não
    pode ser desfeito por um redeploy.
    """
    _rodar(banco)
    with banco.begin() as c:
        c.execute(text(
            "UPDATE conversation_ai_state SET enabled = 1, motivo = NULL "
            "WHERE contact_ref = '5547111' AND channel = 'whatsapp'"))
    _rodar(banco)
    with banco.begin() as c:
        ligado = c.execute(text(
            "SELECT enabled FROM conversation_ai_state "
            "WHERE contact_ref='5547111' AND channel='whatsapp'")).scalar()
    assert ligado in (1, True), "o redeploy re-pausou uma conversa que o operador religou"


def test_NAO_pausa_quem_hoje_esta_no_ar(banco):
    """
    O erro simétrico, e o mais perigoso: calar conversa que está funcionando.

    Havia DOIS portões antigos, cobrindo conjuntos diferentes. No modo com CRM, o
    pipeline ia direto para o campo do GHL — ele NÃO olhava `qualified_leads`. Só
    o portão do `ai_service` olhava, e só quando `qualification_enabled` estava
    ligado. Então um tenant com CRM que desligou a qualificação depois de ter
    leads antigos está no ar hoje, e um backfill ingênuo o emudeceria no deploy.
    """
    with banco.begin() as c:
        c.execute(text("INSERT INTO tenants VALUES ('loc_crm','Com CRM','ghl')"))
        c.execute(text("INSERT INTO ai_agents (location_id, channel, is_active, "
                       "qualification_enabled) VALUES ('loc_crm','whatsapp',1,0)"))
        c.execute(text("INSERT INTO qualified_leads (location_id, phone) "
                       "VALUES ('loc_crm','5599')"))
    _rodar(banco)
    with banco.begin() as c:
        n = c.execute(text(
            "SELECT count(*) FROM conversation_ai_state WHERE location_id='loc_crm'")).scalar()
    assert n == 0, "calou uma conversa que hoje está no ar"


def test_modo_CRM_com_qualificacao_LIGADA_continua_pausado(banco):
    """O outro lado: aí o portão do `ai_service` pausava, e tem que continuar."""
    with banco.begin() as c:
        c.execute(text("INSERT INTO tenants VALUES ('loc_q','Com CRM','ghl')"))
        c.execute(text("INSERT INTO ai_agents (location_id, channel, is_active, "
                       "qualification_enabled) VALUES ('loc_q','whatsapp',1,1)"))
        c.execute(text("INSERT INTO qualified_leads (location_id, phone) "
                       "VALUES ('loc_q','5588')"))
    _rodar(banco)
    with banco.begin() as c:
        n = c.execute(text(
            "SELECT count(*) FROM conversation_ai_state "
            "WHERE location_id='loc_q' AND enabled = 0")).scalar()
    assert n == 1, "despausou um lead que hoje está pausado"


def test_lead_sem_agente_no_canal_nao_gera_linha_orfa(banco):
    """Tenant sem agente naquele canal não deve ganhar estado para canal nenhum."""
    with banco.begin() as c:
        c.execute(text("INSERT INTO tenants VALUES ('loc2','Outro','whatsapp_only')"))
        c.execute(text("INSERT INTO qualified_leads (location_id, phone) VALUES ('loc2','999')"))
    _rodar(banco)
    with banco.begin() as c:
        assert c.execute(text(
            "SELECT count(*) FROM conversation_ai_state WHERE location_id='loc2'")).scalar() == 0
