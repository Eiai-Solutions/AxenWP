"""
Cada canal lê a config do SEU agente.

Estes são bugs LATENTES: com um agente por instância — o caso de hoje em produção
— `.first()` sem filtro de canal acerta por sorte. Ativam no minuto em que o
segundo agente é criado, que é exatamente o que o painel vai passar a permitir.

E ativam em SILÊNCIO. Nenhum dos dois gera erro:

- `_debounce_seconds` pegava o tempo de um agente qualquer: bastava existir um
  agente de Telegram para o WhatsApp passar a usar a janela dele;
- `ai_is_enabled` (modo whatsapp_only) lia `is_active` de um agente qualquer —
  **pausar o agente do Telegram desligava a IA do WhatsApp.**
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from data.models import AIAgent, Base, Tenant

LOC = "wp_dois_canais"


@pytest.fixture
def dois_agentes(tmp_path, monkeypatch):
    """WhatsApp ativo com debounce 3s; Telegram PAUSADO com debounce 30s."""
    import data.database as dbmod
    import services.inbound_pipeline as ip

    engine = create_engine(f"sqlite:///{tmp_path}/canal.db",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    # `inbound_pipeline` importa SessionLocal DENTRO das funcoes, entao o patch
    # tem que ser na origem.
    monkeypatch.setattr(dbmod, "SessionLocal", Session, raising=True)

    db = Session()
    db.add(Tenant(location_id=LOC, company_name="Dois Canais", mode="whatsapp_only"))
    # Telegram entra PRIMEIRO de propósito: assim o `.first()` sem filtro tende a
    # devolvê-lo, e o teste falha de verdade quando a correção sai.
    db.add(AIAgent(location_id=LOC, channel="telegram", name="Bot",
                   prompt="p", is_active=False, debounce_seconds=30))
    db.add(AIAgent(location_id=LOC, channel="whatsapp", name="Sofia",
                   prompt="p", is_active=True, debounce_seconds=3))
    db.commit()
    db.close()
    return SimpleNamespace(Session=Session, ip=ip)


def test_debounce_e_o_do_canal_que_recebeu(dois_agentes):
    ip = dois_agentes.ip

    assert ip._debounce_seconds(LOC, "whatsapp") == 3.0
    assert ip._debounce_seconds(LOC, "telegram") == 30.0


@pytest.mark.asyncio
async def test_pausar_o_telegram_nao_desliga_a_ia_do_whatsapp(dois_agentes):
    """
    O pior dos dois. O agente do WhatsApp está ATIVO; o do Telegram, pausado.
    Sem o filtro de canal, o lead do WhatsApp deixava de ser atendido — sem um
    único erro no log, e sem ninguém relacionar a causa ao Telegram.
    """
    ip = dois_agentes.ip
    tenant = SimpleNamespace(location_id=LOC, mode="whatsapp_only")

    ligada = await ip.ai_is_enabled(tenant, LOC, "5547999", None, "whatsapp")
    assert ligada is True, "pausar o Telegram derrubou a IA do WhatsApp"

    desligada = await ip.ai_is_enabled(tenant, LOC, "5547999", None, "telegram")
    assert desligada is False, "o agente pausado respondeu assim mesmo"


@pytest.mark.asyncio
async def test_canal_sem_agente_nao_atende(dois_agentes):
    """Canal que não tem agente não pode herdar o `is_active` de outro."""
    ip = dois_agentes.ip
    tenant = SimpleNamespace(location_id=LOC, mode="whatsapp_only")

    assert await ip.ai_is_enabled(tenant, LOC, "5547999", None, "instagram") is False


def test_debounce_cai_no_padrao_quando_o_canal_nao_tem_agente(dois_agentes):
    ip = dois_agentes.ip
    # Não herda os 30s do Telegram nem os 3s do WhatsApp.
    assert ip._debounce_seconds(LOC, "instagram") not in (3.0, 30.0)


# ── As telas por instância: determinísticas, não arbitrárias ──

def test_a_tela_escolhe_agente_de_forma_deterministica(dois_agentes):
    """
    Enquanto não há seletor de agente nessas telas, a escolha precisa ao menos ser
    ESTÁVEL — senão o operador vê o campo de qualificação de um agente diferente a
    cada refresh, e não tem como perceber que isso está acontecendo.
    """
    from admin.ai_agent import _agente_da_tela

    db = dois_agentes.Session()
    try:
        escolhas = {_agente_da_tela(db, LOC).id for _ in range(5)}
        assert len(escolhas) == 1, "a tela escolheu agentes diferentes entre chamadas"

        assert _agente_da_tela(db, LOC, "whatsapp").channel == "whatsapp"
        assert _agente_da_tela(db, LOC, "telegram").channel == "telegram"
        assert _agente_da_tela(db, LOC, "instagram") is None
    finally:
        db.close()


# ── A cópia Z-API do caminho de entrada ──
# `zapi_receiver` é um segundo caminho de WhatsApp, fora do pipeline compartilhado.
# Os dois bugs acima foram corrigidos lá e ficaram de pé AQUI.

def test_zapi_le_o_agente_do_whatsapp_e_nao_o_primeiro_que_aparecer(dois_agentes, monkeypatch):
    """
    REGRESSÃO — o mesmo kill-switch, na cópia que ficou para trás.

    Com o Telegram PAUSADO e o WhatsApp ATIVO, `.first()` sem filtro devolvia o
    Telegram e a IA do WhatsApp nascia desligada. Nenhum erro no log: o lead
    simplesmente deixa de ser atendido.
    """
    import data.database as dbmod
    from data.models import AIAgent as A

    db = dbmod.SessionLocal()
    try:
        pausado = (
            db.query(A)
            .filter(A.location_id == LOC, A.channel == "whatsapp")
            .first()
        )
        assert pausado is not None and pausado.is_active is True
        # O de Telegram existe, está pausado, e foi inserido primeiro.
        tg = db.query(A).filter(A.location_id == LOC, A.channel == "telegram").first()
        assert tg.is_active is False
    finally:
        db.close()

    import webhooks.zapi_receiver as zr
    import inspect

    fonte = inspect.getsource(zr.process_inbound_message)
    # As duas queries de AIAgent deste caminho precisam declarar o canal.
    consultas = fonte.count("_AIAgent2.location_id == location_id") + \
        fonte.count("_AIAgent.location_id == location_id")
    canais = fonte.count('_AIAgent2.channel == "whatsapp"') + \
        fonte.count('_AIAgent.channel == "whatsapp"')
    assert consultas == canais == 2, (
        f"{consultas} consultas de agente, {canais} com filtro de canal — "
        "toda consulta neste caminho tem que declarar o canal"
    )


# ── Deletar é irreversível: tem que ser de UM registro ──

def test_deletar_agente_nao_apaga_o_conjunto(dois_agentes, monkeypatch):
    """
    REGRESSÃO — era `.filter(...).delete()`, delete de CONJUNTO. Hoje a trava
    UNIQUE esconde o estrago; com dois agentes no mesmo canal, um clique em
    "remover" apagaria os dois. Sem volta e sem aviso.
    """
    import inspect
    import admin.ai_agent as aa

    # Só o CÓDIGO: o docstring da função cita `.delete()` justamente para explicar
    # o bug, e olhar a fonte crua faria o teste acusar a própria documentação.
    fonte = inspect.getsource(aa.delete_agent_by_channel)
    corpo = "\n".join(
        l for l in fonte.splitlines()
        if not l.lstrip().startswith(("#", '"""', "Antes isto")) 
    ).split('"""')[-1]

    assert ").delete()" not in corpo, "voltou a deletar por filtro, não por registro"
    assert "db.delete(agente)" in fonte, "precisa deletar o registro encontrado"
    assert "agente.channel ==" in fonte, \
        "o guard do canal principal precisa valer pelo agente ENCONTRADO, senão agent_id o contorna"


# ── Restaurar prompt no agente certo ──

def test_restaurar_versao_obedece_ao_agent_id_e_nao_ao_canal(dois_agentes, monkeypatch):
    """
    REGRESSÃO — `restore_version` achava o agente por (location_id, channel) e
    pegava o `.first()`. O prompt É o agente: restaurar no errado troca a persona
    de quem está atendendo, por cima de produção e em silêncio.

    A versão gravada aqui aponta para o agente de TELEGRAM (`agent_id`) mas carrega
    `channel="whatsapp"`. É construído assim de propósito: enquanto a trava
    `UNIQUE(location_id, channel)` existir não dá para ter dois agentes no mesmo
    canal, e sem divergir os dois campos o fallback por canal acertaria por sorte —
    o teste passaria sem a correção, que foi o que aconteceu na primeira versão
    deste arquivo. Divergindo, ele mede a única coisa que interessa: quem manda é
    o `agent_id`.
    """
    import data.database as dbmod
    import services.prompt_history as ph
    from data.models import AIAgent as A

    monkeypatch.setattr(ph, "SessionLocal", dbmod.SessionLocal, raising=True)

    db = dbmod.SessionLocal()
    wa = db.query(A).filter(A.location_id == LOC, A.channel == "whatsapp").first()
    tg = db.query(A).filter(A.location_id == LOC, A.channel == "telegram").first()
    wa_id, tg_id = wa.id, tg.id
    db.close()

    vid = ph.snapshot_prompt(location_id=LOC, channel="whatsapp",
                             prompt="PROMPT QUE PERTENCE AO BOT DE TELEGRAM",
                             source="manual_save", agent_id=tg_id)
    assert vid, "nao gravou a snapshot"

    assert ph.restore_version(vid) is not None

    db = dbmod.SessionLocal()
    depois_wa = db.query(A).filter(A.id == wa_id).first().prompt
    depois_tg = db.query(A).filter(A.id == tg_id).first().prompt
    db.close()

    assert depois_tg == "PROMPT QUE PERTENCE AO BOT DE TELEGRAM", "nao restaurou no dono da versao"
    assert depois_wa == "p", "restaurou no agente errado — trocou a persona de quem atende"


def test_versao_legada_sem_agent_id_ainda_restaura(dois_agentes, monkeypatch):
    """O fallback por canal precisa sobreviver: histórico gravado antes da coluna."""
    import data.database as dbmod
    import services.prompt_history as ph
    from data.models import AIAgent as A

    monkeypatch.setattr(ph, "SessionLocal", dbmod.SessionLocal, raising=True)

    vid = ph.snapshot_prompt(location_id=LOC, channel="telegram",
                             prompt="VERSAO ANTIGA", source="manual_save")
    assert ph.restore_version(vid) is not None

    db = dbmod.SessionLocal()
    tg = db.query(A).filter(A.location_id == LOC, A.channel == "telegram").first().prompt
    db.close()
    assert tg == "VERSAO ANTIGA", "historico legado ficou impossivel de restaurar"


# ── Lacunas que a revisão adversarial pegou na própria correção ──

def test_a_tela_resolve_o_alias_como_o_runtime_resolve(dois_agentes):
    """
    `ai_service` resolve `linked_to_channel` antes de usar a config. A tela não
    resolvia: com Telegram espelhando o WhatsApp, ela lia a linha-espelho — que só
    tem name/prompt/linked_to_channel — e mostrava "sem qualificação" para um
    agente que, no runtime, qualifica.
    """
    from admin.ai_agent import _agente_da_tela

    db = dois_agentes.Session()
    try:
        wpp = db.query(AIAgent).filter_by(location_id=LOC, channel="whatsapp").first()
        wpp.qualification_fields = [{"label": "Qual o orcamento?", "key": "orcamento"}]
        espelho = db.query(AIAgent).filter_by(location_id=LOC, channel="telegram").first()
        espelho.linked_to_channel = "whatsapp"
        espelho.qualification_fields = None
        db.commit()

        achado = _agente_da_tela(db, LOC, "telegram")
        assert achado.channel == "whatsapp", "a tela leu o espelho, nao o agente real"
        assert achado.qualification_fields[0]["label"] == "Qual o orcamento?"
    finally:
        db.close()


def test_alias_apontando_para_canal_inexistente_nao_derruba(dois_agentes):
    """Espelho órfão devolve a própria linha em vez de estourar."""
    from admin.ai_agent import _agente_da_tela

    db = dois_agentes.Session()
    try:
        espelho = db.query(AIAgent).filter_by(location_id=LOC, channel="telegram").first()
        espelho.linked_to_channel = "canal_que_sumiu"
        db.commit()

        assert _agente_da_tela(db, LOC, "telegram").channel == "telegram"
    finally:
        db.close()


def test_restore_grava_a_propria_snapshot_com_o_dono(tmp_path, monkeypatch):
    """
    A correção fez o restore LER `agent_id` para achar o dono — e a snapshot que ele
    grava logo depois nascia com `agent_id=None`, reintroduzindo na versão mais
    recente a ambiguidade que a leitura veio resolver.
    """
    import data.database as dbmod
    import services.prompt_history as ph
    from data.models import AgentPromptHistory

    engine = create_engine(f"sqlite:///{tmp_path}/hist.db",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(dbmod, "SessionLocal", Session, raising=True)
    monkeypatch.setattr(ph, "SessionLocal", Session, raising=True)

    db = Session()
    db.add(Tenant(location_id=LOC, company_name="X"))
    ag = AIAgent(location_id=LOC, channel="whatsapp", name="Sofia", prompt="atual")
    db.add(ag)
    db.commit()
    db.refresh(ag)
    antiga = AgentPromptHistory(location_id=LOC, channel="whatsapp", agent_id=ag.id,
                                source="manual_save", prompt="prompt antigo")
    db.add(antiga)
    db.commit()
    hid, agente_id = antiga.id, ag.id
    db.close()

    assert ph.restore_version(hid) is not None

    db = Session()
    try:
        nova = (db.query(AgentPromptHistory)
                  .filter_by(source="restore").order_by(AgentPromptHistory.id.desc()).first())
        assert nova is not None, "o restore nao gravou snapshot"
        assert nova.agent_id == agente_id, "a snapshot do restore nasceu orfa"
        assert nova.channel == "whatsapp"
    finally:
        db.close()
