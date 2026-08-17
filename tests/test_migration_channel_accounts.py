"""
Migration 034: a conta de canal passa a existir, sem mudar comportamento.

O que este arquivo garante, e que só se prova rodando:

1. **O backfill acerta o provedor ATIVO.** Copiar os dois conjuntos de credencial
   criaria duas contas para o mesmo número.
2. **É idempotente.** A migration roda no boot, toda vez. Rodar duas vezes não pode
   duplicar conta nem religar agente que alguém desligou à mão.
3. **Não muda comportamento.** Nenhum agente perde config, e as colunas do tenant
   continuam intactas — é isso que faz o rollback para o release anterior não
   perder credencial.

O ambiente imita o de produção: `create_all` primeiro (que já cria a tabela nova,
porque o modelo existe) e a migration depois — a ordem do lifespan.
"""

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from data.models import AIAgent, Base, ChannelAccount, Tenant


def _carrega_migration():
    caminho = Path(__file__).resolve().parents[1] / "alembic/versions/034_channel_accounts.py"
    spec = importlib.util.spec_from_file_location("mig034", caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def banco(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/ca.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    db = Session()
    # WAHA + Telegram na mesma instância: duas contas.
    db.add(Tenant(location_id="loc_waha", company_name="Eiai Solutions", mode="ghl",
                  whatsapp_provider="waha", waha_base_url="https://w",
                  waha_session="sessao-eiai", waha_api_key="k",
                  telegram_bot_token="tg:123", telegram_bot_username="@eiaibot"))
    # Z-API: uma conta.
    db.add(Tenant(location_id="loc_zapi", company_name="Cliente Z", mode="whatsapp_only",
                  whatsapp_provider="zapi", zapi_instance_id="INST-9", zapi_token="tok"))
    # Nenhum canal configurado: nenhuma conta, e não pode explodir.
    db.add(Tenant(location_id="loc_vazio", company_name="Sem canal", whatsapp_provider="zapi"))
    for loc, ch, nome in [("loc_waha", "whatsapp", "Ellen"), ("loc_waha", "telegram", "Bot"),
                          ("loc_zapi", "whatsapp", "Sofia")]:
        db.add(AIAgent(location_id=loc, channel=ch, name=nome, prompt="p", model="m"))
    db.commit()
    db.close()
    return engine, Session


def _migra(engine, vezes=1):
    mod = _carrega_migration()
    for _ in range(vezes):
        with engine.begin() as bind:
            mod._backfill(bind)


def test_backfill_cria_uma_conta_por_canal_configurado(banco):
    engine, Session = banco
    _migra(engine)

    db = Session()
    contas = db.query(ChannelAccount).order_by(ChannelAccount.id).all()
    resumo = sorted((c.location_id, c.channel, c.external_ref) for c in contas)
    db.close()

    assert resumo == [
        ("loc_waha", "telegram", "@eiaibot"),
        ("loc_waha", "whatsapp", "sessao-eiai"),
        ("loc_zapi", "whatsapp", "INST-9"),
    ], "o tenant sem canal nao pode gerar conta, e cada canal gera exatamente uma"


def test_a_conta_de_whatsapp_leva_a_credencial_do_provedor_ATIVO(banco):
    """Copiar os dois conjuntos criaria duas contas para o mesmo numero."""
    engine, Session = banco
    _migra(engine)

    db = Session()
    waha = db.query(ChannelAccount).filter_by(location_id="loc_waha", channel="whatsapp").one()
    zapi = db.query(ChannelAccount).filter_by(location_id="loc_zapi", channel="whatsapp").one()
    dados = (waha.waha_session, waha.zapi_instance_id, zapi.zapi_instance_id, zapi.waha_session)
    db.close()

    assert dados == ("sessao-eiai", None, "INST-9", None)


def test_cada_agente_fica_ligado_a_conta_do_SEU_canal(banco):
    engine, Session = banco
    _migra(engine)

    db = Session()
    ligacoes = {}
    for a in db.query(AIAgent).all():
        conta = db.query(ChannelAccount).filter_by(id=a.channel_account_id).one()
        ligacoes[(a.location_id, a.channel)] = (conta.location_id, conta.channel)
    db.close()

    assert ligacoes == {
        ("loc_waha", "whatsapp"): ("loc_waha", "whatsapp"),
        ("loc_waha", "telegram"): ("loc_waha", "telegram"),
        ("loc_zapi", "whatsapp"): ("loc_zapi", "whatsapp"),
    }, "agente ligado a conta de outro canal (ou de outro tenant)"


def test_rodar_de_novo_nao_duplica(banco):
    """A migration roda no boot, toda vez."""
    engine, Session = banco
    _migra(engine, vezes=3)

    db = Session()
    total = db.query(ChannelAccount).count()
    db.close()
    assert total == 3, f"{total} contas depois de 3 execucoes — duplicou"


def test_nao_religa_agente_que_alguem_desligou_a_mao(banco):
    """Se um operador reapontou o agente, a migration nao desfaz."""
    engine, Session = banco
    _migra(engine)

    db = Session()
    outra = ChannelAccount(location_id="loc_waha", channel="whatsapp",
                           external_ref="segundo-numero", label="Suporte")
    db.add(outra)
    db.commit()
    alvo = db.query(AIAgent).filter_by(location_id="loc_waha", channel="whatsapp").one()
    alvo.channel_account_id = outra.id
    db.commit()
    escolhida = outra.id
    db.close()

    _migra(engine)

    db = Session()
    depois = db.query(AIAgent).filter_by(location_id="loc_waha", channel="whatsapp").one()
    ficou = depois.channel_account_id
    db.close()
    assert ficou == escolhida, "a migration sobrescreveu uma ligacao feita a mao"


def test_as_colunas_do_tenant_continuam_intactas(banco):
    """Dual-write: rollback para o release anterior nao pode perder credencial."""
    engine, Session = banco
    _migra(engine)

    db = Session()
    t = db.query(Tenant).filter_by(location_id="loc_waha").one()
    dados = (t.waha_session, t.waha_api_key, t.telegram_bot_token)
    db.close()
    assert dados == ("sessao-eiai", "k", "tg:123")


# ── O caminho que PRODUÇÃO recebe ──
#
# O ponto cego que a revisão adversarial expôs: os testes acima montam o schema com
# `create_all` e chamam só `_backfill()`. Mas produção não recebe isso — lá
# `ai_agents` já existe, `create_all` NÃO altera tabela existente, e quem mexe no
# schema é o `upgrade()`. A suíte inteira passava sem nunca exercitar o DDL real, e
# foi por isso que a FK faltando passou batido.

def _alembic(engine, acao, revisao):
    """
    Roda o alembic de verdade contra ESTE banco.

    `alembic/env.py:42` monta o engine a partir de `data.database.
    SQLALCHEMY_DATABASE_URL`, e ignora o `sqlalchemy.url` do Config — então apontar
    o módulo é a única forma de o comando cair no banco do teste em vez do de dev.
    """
    import data.database as dbmod
    from alembic import command
    from alembic.config import Config

    antes = dbmod.SQLALCHEMY_DATABASE_URL
    dbmod.SQLALCHEMY_DATABASE_URL = str(engine.url)
    try:
        (command.upgrade if acao == "upgrade" else command.stamp)(Config("alembic.ini"), revisao)
    finally:
        dbmod.SQLALCHEMY_DATABASE_URL = antes


def _sobe_migrations(engine, ate="head"):
    _alembic(engine, "upgrade", ate)


def _marca(engine, revisao):
    _alembic(engine, "stamp", revisao)


@pytest.fixture
def banco_como_producao(tmp_path):
    """
    Schema montado e alembic carimbado em 033 — o `upgrade()` roda de verdade.

    LIMITE HONESTO deste arquivo: aqui o schema vem do `create_all`, então
    `ai_agents` já nasce com a FK. Produção recebe a coluna pela MIGRATION, e foi
    exatamente aí que a FK faltando passou batido. Reproduzir o pré-estado exato
    esbarra no SQLite, que recusa dropar coluna referenciada por FK.

    O que estes testes cobrem: o `upgrade()` roda ponta a ponta sem estourar (é o
    caminho do boot, e migration que falha derruba o app), o backfill acontece pelo
    ponto de entrada real, as datas entram e reexecutar é no-op.

    O que NÃO cobrem: a criação da constraint em si, que só existe no Postgres.
    Isso é verificado no teste seguinte, por leitura do código — e conferido à mão
    contra o banco de produção.
    """
    engine = create_engine(f"sqlite:///{tmp_path}/prod.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)

    with engine.begin() as c:
        c.execute(text(
            "INSERT INTO tenants (location_id, company_name, whatsapp_provider, "
            "waha_base_url, waha_session, waha_api_key, mode) "
            "VALUES ('loc_p','Eiai Solutions','waha','https://w','sessao','k','ghl')"
        ))
        c.execute(text(
            "INSERT INTO ai_agents (location_id, channel, name, prompt, model) "
            "VALUES ('loc_p','whatsapp','Ellen','p','m')"
        ))
    _marca(engine, "033")
    return engine


def test_a_035_cria_a_FK_no_postgres_e_limpa_orfao_antes(banco_como_producao):
    """
    A FK só entra no Postgres — o SQLite não suporta ALTER de constraint. Como a
    suíte roda em SQLite, o que dá para garantir aqui é que a migration MANDA criar,
    com o gate de dialeto e a limpeza de ponteiro pendurado antes (senão a
    constraint não entra e a migration derruba o boot).

    Confirmado à mão contra produção: antes desta migration, `ai_agents` tinha só a
    FK de `location_id`.
    """
    import inspect as _i
    mod = _carrega_035()
    fonte = _i.getsource(mod.upgrade)

    assert 'dialect.name == "postgresql"' in _i.getsource(mod), "sem gate de dialeto"
    assert "create_foreign_key" in fonte, "a migration nao cria a constraint"
    assert 'ondelete="SET NULL"' in fonte, \
        "apagar a conta nao pode apagar o agente junto nem travar o delete"
    assert fonte.index("channel_account_id = NULL") < fonte.index("create_foreign_key"), \
        "a limpeza de orfao tem que vir ANTES da constraint, senao ela nao entra"


def _carrega_035():
    caminho = Path(__file__).resolve().parents[1] / "alembic/versions/035_fk_e_datas_da_conta.py"
    spec = importlib.util.spec_from_file_location("mig035", caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_upgrade_de_verdade_cria_coluna_e_liga_o_agente(banco_como_producao):
    engine = banco_como_producao
    _sobe_migrations(engine)

    with engine.connect() as c:
        conta = c.execute(text("SELECT id, external_ref, created_at FROM channel_accounts")).first()
        agente = c.execute(text("SELECT channel_account_id FROM ai_agents")).first()

    assert conta is not None, "a migration nao criou a conta no caminho de producao"
    assert conta[1] == "sessao"
    assert conta[2] is not None, "created_at nasceu NULL — nao da para reconstruir depois"
    assert agente[0] == conta[0], "o agente nao ficou ligado a conta"


def test_upgrade_roda_duas_vezes_sem_quebrar(banco_como_producao):
    """O boot roda `upgrade head` sempre; a segunda vez tem que ser no-op."""
    engine = banco_como_producao
    _sobe_migrations(engine)
    _sobe_migrations(engine)

    with engine.connect() as c:
        total = c.execute(text("SELECT count(*) FROM channel_accounts")).scalar()
    assert total == 1


def test_a_035_preenche_data_que_a_034_deixou_nula(banco_como_producao):
    """Simula produção: conta já criada sem data, como está lá hoje."""
    engine = banco_como_producao
    _sobe_migrations(engine, ate="034")

    with engine.begin() as c:
        c.execute(text("UPDATE channel_accounts SET created_at = NULL, updated_at = NULL"))

    _sobe_migrations(engine)

    with engine.connect() as c:
        datas = c.execute(text("SELECT created_at, updated_at FROM channel_accounts")).first()
    assert datas[0] is not None and datas[1] is not None
