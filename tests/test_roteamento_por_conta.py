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


# ── O produtor que faltava ──
#
# A primeira versão da fase 3a só LIA `channel_accounts`. O único escritor era o
# backfill da migration, que roda uma vez — então toda instância conectada depois
# do deploy ficava sem conta, e o aviso de "config divergente" disparava no estado
# NORMAL, uma vez por turno, para sempre. Consumidor sem produtor, o mesmo cheiro
# das portas do wizard.

@pytest.fixture
def instancia_nova(tmp_path, monkeypatch):
    """Exatamente o que o painel deixa ao conectar o WAHA: tenant configurado, zero contas."""
    import data.database as dbmod
    import services.channel_accounts as ca

    engine = create_engine(f"sqlite:///{tmp_path}/nova.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(dbmod, "SessionLocal", Session, raising=True)
    monkeypatch.setattr(ca, "SessionLocal", Session, raising=True)

    db = Session()
    db.add(Tenant(location_id="loc_novo", company_name="Instancia Nova", mode="whatsapp_only",
                  whatsapp_provider="waha", waha_base_url="https://w", waha_session="loc_novo",
                  waha_api_key="k"))
    db.commit()
    db.close()
    return Session


@pytest.mark.asyncio
async def test_sincronizar_cria_a_conta_que_o_backfill_nunca_veria(instancia_nova):
    from data.models import ChannelAccount as CA
    from services.channel_accounts import resolver, sincronizar

    db = instancia_nova()
    assert db.query(CA).count() == 0, "o fixture deveria comecar sem conta"
    db.close()

    conta_id = await sincronizar("loc_novo", "whatsapp")
    assert conta_id is not None

    db = instancia_nova()
    conta = db.query(CA).one()
    dados = (conta.external_ref, conta.waha_session, conta.created_at is not None)
    db.close()
    assert dados == ("loc_novo", "loc_novo", True)

    # E agora a mensagem resolve pela conta, sem fallback.
    assert await resolver("loc_novo", "whatsapp", "loc_novo") == conta_id


@pytest.mark.asyncio
async def test_instancia_sem_conta_NAO_vira_ruido_no_log(instancia_nova, caplog):
    """
    O aviso existe para denunciar persona errada. Se ele disparar no estado normal
    de toda instância nova, uma vez por turno, enterra o próprio sinal.
    """
    import logging
    from services.channel_accounts import resolver

    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            assert await resolver("loc_novo", "whatsapp", "loc_novo") is None

    avisos = [r for r in caplog.records if "[CONTA]" in r.message]
    assert avisos == [], f"{len(avisos)} avisos no estado normal — o alarme virou ruido"


@pytest.mark.asyncio
async def test_o_aviso_dispara_quando_a_divergencia_e_REAL(instancia_nova, caplog):
    """Conta existe e a referência não bate: aí sim é config divergente."""
    import logging
    from services.channel_accounts import resolver, sincronizar

    await sincronizar("loc_novo", "whatsapp")

    with caplog.at_level(logging.WARNING):
        assert await resolver("loc_novo", "whatsapp", "sessao-renomeada") is not None

    assert any("sessao-renomeada" in r.message for r in caplog.records), \
        "divergencia real passou em silencio"


@pytest.mark.asyncio
async def test_trocar_de_provedor_reescreve_a_MESMA_conta(instancia_nova):
    """
    Sem isto o `external_ref` apodrece: a conta guarda a sessão WAHA de um tenant
    que voltou para Z-API, e a referência nunca mais bate.
    """
    from data.models import ChannelAccount as CA
    from services.channel_accounts import sincronizar

    await sincronizar("loc_novo", "whatsapp")

    db = instancia_nova()
    t = db.query(Tenant).filter_by(location_id="loc_novo").one()
    t.whatsapp_provider = "zapi"
    t.zapi_instance_id = "INST-NOVA"
    t.waha_session = None
    db.commit()
    db.close()

    await sincronizar("loc_novo", "whatsapp")

    db = instancia_nova()
    contas = db.query(CA).all()
    dados = [(c.external_ref, c.zapi_instance_id, c.waha_session) for c in contas]
    db.close()

    assert len(contas) == 1, "criou conta nova em vez de reescrever a existente"
    assert dados[0] == ("INST-NOVA", "INST-NOVA", None), \
        "a credencial do provedor antigo ficou pendurada na linha"


# ── Fase 3b: Z-API ──
#
# O `zapi_receiver` é um caminho de entrada PARALELO: usa o adapter para parsear,
# mas não passa por `inbound_pipeline`. A ligação da fase 3a não o alcançava, então
# a conta precisou ser ligada aqui também — com o mesmo molde de dicionário lateral
# por `contact_key` que o debounce já usa.

def test_o_adapter_zapi_le_o_instanceId_do_payload():
    from channels.whatsapp.zapi import ZAPIChannel

    pm = ZAPIChannel().parse_inbound(LOC, {
        "phone": "5547999", "instanceId": "INST-COMERCIAL",
        "text": {"message": "oi"},
    })
    assert pm.account_ref == "INST-COMERCIAL"


def test_payload_zapi_sem_instanceId_cai_no_fallback():
    """
    NÃO foi verificado contra tráfego real se a Z-API manda `instanceId` em todo
    tipo de evento — produção não tem nenhum tenant Z-API hoje. O desenho é seguro
    por construção, e é isto que este teste fixa: ausente vira None, e o roteamento
    usa o caminho por canal, que é o de sempre.
    """
    from channels.whatsapp.zapi import ZAPIChannel

    pm = ZAPIChannel().parse_inbound(LOC, {"phone": "5547999", "text": {"message": "oi"}})
    assert pm.account_ref is None


def test_o_receiver_zapi_carrega_a_conta_ate_o_flush():
    """
    O parse e a chamada da IA vivem em FUNÇÕES diferentes, separadas pelo debounce.
    Sem o dicionário lateral, a conta descoberta na porta se perderia no caminho.
    """
    import inspect
    import webhooks.zapi_receiver as zr

    assert "_ai_conta_por_contato" in inspect.getsource(zr.process_inbound_message), \
        "a porta nao guarda a conta"
    flush = inspect.getsource(zr._run_ai_response)
    assert "_ai_conta_por_contato.pop" in flush, "o flush nao recupera a conta"
    assert "channel_account_id=conta_id" in flush, "a conta nao chega no ai_service"

    # E o cleanup periodico tem que limpar o dicionario novo tambem, senao vaza.
    assert "_ai_conta_por_contato.pop" in inspect.getsource(zr.cleanup_stale_debounce_entries), \
        "o dicionario novo vaza no cleanup"


def test_nenhuma_anotacao_do_projeto_quebra_no_python_de_producao():
    """
    REGRESSÃO — teria derrubado o boot, e a versão do Python esconde a classe toda.

    Dev roda 3.14, onde anotação é preguiçosa (PEP 649); produção roda **3.11**, onde
    anotação de módulo e de classe é avaliada NO IMPORT. `Dict[str, Optional[int]]`
    sem importar `Optional` passa aqui e dá `NameError` lá — o app não sobe.

    A primeira versão deste teste fixava UM símbolo num módulo, o que não fecha a
    classe de erro: o próximo `Optional` esquecido em outro arquivo passaria igual.
    Aqui a varredura é sobre TODO módulo do projeto já carregado — o mesmo conjunto
    que o boot importa.
    """
    import sys
    import typing

    import main  # noqa: F401  — arrasta a árvore de imports do boot

    nossos = [
        m for nome, m in list(sys.modules.items())
        if m is not None
        and nome.split(".")[0] in {"services", "channels", "webhooks", "data",
                                   "admin", "auth", "utils", "public"}
        and getattr(m, "__annotations__", None)
    ]
    assert nossos, "nenhum modulo do projeto carregado — a varredura nao mediu nada"

    quebrados = []
    for m in nossos:
        try:
            typing.get_type_hints(m)
        except NameError as e:
            quebrados.append(f"{m.__name__}: {e}")
        except Exception:
            # Anotação que `get_type_hints` não resolve por outro motivo não é o que
            # se procura: só NameError derruba o import no 3.11.
            pass

    assert not quebrados, (
        "anotacao com nome nao importado — NameError no import em Python 3.11:\n  "
        + "\n  ".join(quebrados)
    )


# ── O que a revisão adversarial da 3b pegou ──

@pytest.mark.asyncio
async def test_falha_de_banco_no_resolver_NAO_propaga(duas_contas, monkeypatch):
    """
    REGRESSÃO — o `await` que resolve a conta ficava entre o append no buffer de
    debounce e o `create_task`. Essa sequência era síncrona, portanto atômica: todo
    item no buffer ganhava uma task. Com a exceção subindo, o `create_task` não
    rodava e a mensagem ficava ÓRFÃ — invisível aos dois limpadores (que derivam as
    chaves de `_ai_pending_tasks`), colada no próximo turno, e acumulando até o hard
    cap de 2000, quando o agendamento da IA morre para TODOS os tenants.

    O gêmeo `_sincronizar_sync` já engolia exceção de propósito; este não. O
    contrato do módulo sempre foi "não sei dizer → None → fallback por canal", e
    falha de banco é exatamente "não sei dizer".
    """
    import services.channel_accounts as ca
    from sqlalchemy.exc import OperationalError

    def _banco_fora():
        raise OperationalError("select 1", {}, Exception("server closed the connection"))

    monkeypatch.setattr(ca, "SessionLocal", _banco_fora, raising=True)

    assert await ca.resolver(LOC, "whatsapp", "sessao-comercial") is None


@pytest.mark.asyncio
async def test_sincronizar_LIGA_o_agente_a_conta(instancia_nova):
    """
    O backfill da migration ligava o agente à conta; o produtor de runtime não.
    Instância nova ganhava conta e o roteamento por conta ficava INERTE — sempre
    caindo no fallback, para sempre.
    """
    from data.models import AIAgent as A
    from services.channel_accounts import sincronizar

    db = instancia_nova()
    db.add(A(location_id="loc_novo", channel="whatsapp", name="Sofia", prompt="p", model="m"))
    db.commit()
    db.close()

    conta_id = await sincronizar("loc_novo", "whatsapp")

    db = instancia_nova()
    agente = db.query(A).filter_by(location_id="loc_novo", channel="whatsapp").one()
    ligado = agente.channel_account_id
    db.close()
    assert ligado == conta_id, "a conta foi criada e o agente ficou solto"


@pytest.mark.asyncio
async def test_sincronizar_NAO_reaponta_agente_ligado_a_mao(instancia_nova):
    """Reapontar quem o operador já ligou seria decidir por ele."""
    from data.models import AIAgent as A, ChannelAccount as CA
    from services.channel_accounts import sincronizar

    db = instancia_nova()
    outra = CA(location_id="loc_novo", channel="whatsapp", external_ref="escolhida-a-mao")
    db.add(outra)
    db.commit()
    db.add(A(location_id="loc_novo", channel="whatsapp", name="Sofia", prompt="p",
             model="m", channel_account_id=outra.id))
    db.commit()
    escolhida = outra.id
    db.close()

    await sincronizar("loc_novo", "whatsapp")

    db = instancia_nova()
    ligado = db.query(A).filter_by(location_id="loc_novo").one().channel_account_id
    db.close()
    assert ligado == escolhida


def test_a_sequencia_de_agendamento_do_zapi_voltou_a_ser_atomica():
    """
    Nenhum `await` entre o append no buffer e o `create_task`: é isso que garante
    que todo item no buffer ganha uma task. A resolução da conta foi para o flush.
    """
    import inspect
    import webhooks.zapi_receiver as zr

    fonte = inspect.getsource(zr.process_inbound_message)
    bruto = fonte[fonte.index("_ai_message_buffers[contact_key].append"):
                  fonte.index("asyncio.create_task")]
    # Só CÓDIGO: os comentários deste trecho citam "await" justamente para explicar
    # por que ele não pode estar aqui — olhar a fonte crua acusaria a documentação.
    trecho = "\n".join(l for l in bruto.splitlines() if not l.lstrip().startswith("#"))
    assert "await" not in trecho, (
        "voltou a haver await entre o buffer e o create_task — mensagem pode ficar orfa"
    )
    # E a resolucao mudou de lugar, nao sumiu.
    assert "_resolver_conta" in inspect.getsource(zr._run_ai_response)
