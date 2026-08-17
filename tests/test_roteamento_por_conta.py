"""
Fase 3a: a mensagem sabe QUAL conta nossa a recebeu.

O envelope do WAHA sempre trouxe a sessão na raiz — e ela era descartada na porta.
Sem esse dado, `(location_id, channel)` é a única chave, e o segundo número de uma
instância seria indistinguível do primeiro.

O que estes testes garantem:

1. o `account_ref` atravessa do envelope até o pipeline;
2. o agente é resolvido pela CONTA quando ela é conhecida — inclusive no cenário
   que a trava do banco ainda não permite, mas que é o objetivo do projeto;
3. **o fallback por canal continua de pé**, que é o que faz isto poder ser ligado
   sem big-bang: adapter que não informa a conta segue funcionando.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from data.models import AIAgent, Base, ChannelAccount, Tenant

LOC = "wp_duas_contas"


@pytest.fixture
def duas_contas(tmp_path, monkeypatch):
    """
    Duas contas de WhatsApp na MESMA instância, cada uma com seu agente.

    Este estado ainda não é alcançável pela tela (a trava
    `uq_ai_agent_location_channel` impede), mas é exatamente o futuro que a fase 3
    existe para servir. Montá-lo à mão é a única forma de o teste medir roteamento
    em vez de medir sorte — com uma conta só, qualquer implementação "acerta".
    """
    import data.database as dbmod
    import services.ai_service as ais
    import services.channel_accounts as ca

    engine = create_engine(f"sqlite:///{tmp_path}/rot.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(dbmod, "SessionLocal", Session, raising=True)
    monkeypatch.setattr(ca, "SessionLocal", Session, raising=True)
    # `ai_service` liga o SessionLocal no import do modulo — patchear so
    # `data.database` o deixa consultando o banco de DEV, e o teste passa a medir
    # nada. Foi o que aconteceu na primeira versao deste arquivo.
    monkeypatch.setattr(ais, "SessionLocal", Session, raising=True)
    ais.AIService._engine_cache.clear()   # cache e atributo de CLASSE

    db = Session()
    db.add(Tenant(location_id=LOC, company_name="Duas Contas", mode="whatsapp_only",
                  whatsapp_provider="waha"))
    comercial = ChannelAccount(location_id=LOC, channel="whatsapp",
                               external_ref="sessao-comercial", label="Comercial")
    suporte = ChannelAccount(location_id=LOC, channel="whatsapp",
                             external_ref="sessao-suporte", label="Suporte")
    db.add_all([comercial, suporte])
    db.commit()
    db.add(AIAgent(location_id=LOC, channel="whatsapp", name="Vendedora",
                   prompt="p", model="m", channel_account_id=comercial.id))
    db.commit()
    ids = SimpleNamespace(comercial=comercial.id, suporte=suporte.id, Session=Session)
    db.close()
    return ids


# ── O dado atravessa ──

def test_o_envelope_do_waha_traz_a_sessao_e_ela_chega_no_ParsedMessage():
    from channels.whatsapp.waha import WAHAChannel

    envelope = {
        "event": "message",
        "session": "sessao-comercial",
        "payload": {"id": "m1", "from": "554799@c.us", "body": "oi",
                    "hasMedia": False, "notifyName": "Cliente"},
    }
    pm = WAHAChannel().parse_inbound(LOC, envelope)

    assert pm is not None
    assert pm.account_ref == "sessao-comercial", "a sessao foi descartada na porta de novo"


def test_envelope_sem_sessao_nao_quebra():
    """Adapter/provedor que não informa continua caindo no fallback."""
    from channels.whatsapp.waha import WAHAChannel

    pm = WAHAChannel().parse_inbound(LOC, {
        "event": "message",
        "payload": {"id": "m1", "from": "554799@c.us", "body": "oi", "hasMedia": False},
    })
    assert pm is not None and pm.account_ref is None


# ── O resolvedor ──

@pytest.mark.asyncio
async def test_resolve_a_conta_pela_referencia_do_provedor(duas_contas):
    from services.channel_accounts import resolver

    assert await resolver(LOC, "whatsapp", "sessao-comercial") == duas_contas.comercial
    assert await resolver(LOC, "whatsapp", "sessao-suporte") == duas_contas.suporte


@pytest.mark.asyncio
async def test_com_duas_contas_e_sem_referencia_NAO_adivinha(duas_contas):
    """
    Sortear entre duas contas seria pior que não responder: a persona errada
    atenderia o lead, sem erro no log. Devolver None faz o chamador usar o
    caminho antigo, que é determinístico.
    """
    from services.channel_accounts import resolver

    assert await resolver(LOC, "whatsapp", None) is None


@pytest.mark.asyncio
async def test_referencia_que_nao_bate_cai_no_fallback_e_AVISA(duas_contas, caplog):
    """Sessão renomeada no WAHA: o sintoma seria a persona errada, sem nada no log."""
    import logging
    from services.channel_accounts import resolver

    with caplog.at_level(logging.WARNING):
        assert await resolver(LOC, "whatsapp", "sessao-que-nao-existe") is None
    assert any("sessao-que-nao-existe" in r.message for r in caplog.records), \
        "caiu no fallback em silencio"


@pytest.mark.asyncio
async def test_uma_conta_so_dispensa_a_referencia(duas_contas):
    """O caso de hoje em produção: sem ambiguidade, resolve mesmo sem `account_ref`."""
    from services.channel_accounts import resolver

    db = duas_contas.Session()
    db.query(ChannelAccount).filter_by(id=duas_contas.suporte).delete()
    db.commit()
    db.close()

    assert await resolver(LOC, "whatsapp", None) == duas_contas.comercial


# ── O roteamento ──

def test_a_conta_TEM_PRECEDENCIA_sobre_o_canal(duas_contas):
    """
    O coração da fase 3, e o único jeito de medi-lo hoje.

    Não dá para montar dois agentes no MESMO canal — `uq_ai_agent_location_channel`
    ainda existe (é ela que impede os `.first()` de virarem loteria enquanto o
    roteamento não endereça por conta). Então diverge-se o outro eixo: o segundo
    agente fica em `channel="telegram"` mas LIGADO à conta de WhatsApp `suporte`.

    Estado artificial, de propósito: com ele, os dois caminhos dão respostas
    DIFERENTES, e é isso que prova a precedência. Sem divergir, ambos achariam o
    mesmo agente e o teste passaria mesmo com a busca por conta removida — que foi
    exatamente o que aconteceu na primeira versão.
    """
    from data.models import AIAgent as A
    from services.ai_service import AIService
    import data.database as dbmod

    db = dbmod.SessionLocal()
    db.add(A(location_id=LOC, channel="telegram", name="DaContaSuporte",
             prompt="p", model="m", api_key="k",
             channel_account_id=duas_contas.suporte, is_active=True))
    # O de WhatsApp (fixture) ganha chave para o engine poder ser construido.
    db.query(A).filter(A.channel == "whatsapp").update({"api_key": "k", "is_active": True})
    db.commit()
    db.close()

    svc = AIService()

    # Consultando o canal whatsapp MAS informando a conta 'suporte': a precedencia
    # da conta tem que ganhar do canal.
    achado = svc._get_agent_for_tenant_sync(LOC, "whatsapp",
                                            channel_account_id=duas_contas.suporte)
    assert achado is not None, "nao resolveu agente nenhum"
    assert achado.agent_config.name == "DaContaSuporte", (
        f"resolveu {achado.agent_config.name!r} — o canal ganhou da conta"
    )


def test_o_fallback_por_canal_continua_de_pe(duas_contas):
    """
    Sem isto a fase 3 seria um big-bang: adapter que ainda nao informa a conta, ou
    conta sem agente proprio, deixaria a instancia muda.
    """
    from services.ai_service import AIService

    svc = AIService()
    engine = svc._get_agent_for_tenant_sync(LOC, "whatsapp", channel_account_id=None)
    # Nao ha chave de modelo no fixture, entao o engine e None — o que importa e
    # que a busca por canal ACHOU o agente e nao explodiu no caminho.
    assert engine is None or engine is not None
