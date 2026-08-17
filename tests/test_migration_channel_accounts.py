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
