"""
O interruptor da IA — a política que antes morava em três lugares que discordavam.

Cada teste aqui guarda um defeito real que existia em produção até 2026-08-20:

  · pausar um número no WhatsApp calava o Telegram dele junto (chave sem canal);
  · o Telegram não tinha gate por conversa nenhum;
  · a pausa por handoff era eterna, então o lead que voltasse nunca mais era atendido;
  · qualificar pausava por efeito colateral de uma linha em `qualified_leads`;
  · o portão do CRM (rede, fail-closed) era consultado antes dos locais.
"""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from data.models import AIAgent, Base, ConversationAIState, Tenant

LOC = "loc_gate"


@pytest.fixture
def banco(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/gate.db",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    db = Session()
    db.add(Tenant(location_id=LOC, company_name="Eiai", mode="whatsapp_only"))
    db.add(AIAgent(location_id=LOC, channel="whatsapp", name="Ellen",
                   prompt="p", model="m", is_active=True))
    db.add(AIAgent(location_id=LOC, channel="telegram", name="Bot",
                   prompt="p", model="m", is_active=True))
    db.commit()
    db.close()

    import data.database as dbmod
    monkeypatch.setattr(dbmod, "SessionLocal", Session, raising=True)
    return Session


def _pode(channel="whatsapp", contato="5547", tenant=None, contact_id=None):
    from services import ai_gate

    return asyncio.run(ai_gate.pode_responder(
        location_id=LOC, channel=channel, contact_ref=contato,
        tenant=tenant or SimpleNamespace(mode="whatsapp_only"), contact_id=contact_id,
    ))


# ── O básico ──

def test_conversa_sem_estado_a_IA_responde(banco):
    """Ausência de linha é 'nunca ninguém pausou', não 'pausado'."""
    assert _pode() is True


def test_agente_desligado_cala_o_canal_inteiro(banco):
    from data.models import AIAgent as A

    db = banco()
    db.query(A).filter(A.channel == "whatsapp").first().is_active = False
    db.commit(); db.close()

    assert _pode(channel="whatsapp") is False
    assert _pode(channel="telegram") is True, "desligar um canal derrubou o outro"


# ── Canal alias ──

def test_canal_ALIAS_responde_pelo_agente_vinculado(banco):
    """
    REGRESSÃO — a linha-espelho de um canal vinculado nasce `is_active=False`.

    `link_channel_to_existing` cria o alias com nome, prompt e ponteiro, e não
    passa `is_active` — o default do ORM é False. Antes deste gate existir, quem
    atendia o Telegram vinculado era `ai_service`, que RESOLVE o alias antes de
    olhar `is_active`. Um gate que lê a linha crua cala o canal inteiro, para
    todos os contatos, sem prazo e sem estado nenhum para religar — e o painel
    esconde o formulário do canal alias, então o operador não contorna nem à mão.
    """
    from data.models import AIAgent as A

    db = banco()
    db.query(A).filter(A.channel == "telegram").delete()
    # Exatamente como o endpoint de vincular cria: sem `is_active`.
    db.add(A(location_id=LOC, channel="telegram", name="Bot",
             prompt="(alias)", linked_to_channel="whatsapp"))
    db.commit(); db.close()

    assert _pode(channel="telegram") is True, (
        "canal vinculado ficou mudo: o gate leu a linha-espelho em vez de resolver o alias"
    )


def test_alias_segue_o_agente_ALVO_quando_ele_e_desligado(banco):
    """Vincular é herdar de verdade — inclusive o desligar."""
    from data.models import AIAgent as A

    db = banco()
    db.query(A).filter(A.channel == "telegram").delete()
    db.add(A(location_id=LOC, channel="telegram", name="Bot",
             prompt="(alias)", linked_to_channel="whatsapp"))
    db.query(A).filter(A.channel == "whatsapp").first().is_active = False
    db.commit(); db.close()

    assert _pode(channel="telegram") is False


def test_alias_apontando_para_canal_inexistente_fica_calado(banco):
    """Fail-closed: responder com agente que ninguém configurou é pior que calar."""
    from data.models import AIAgent as A

    db = banco()
    db.query(A).delete()
    db.add(A(location_id=LOC, channel="telegram", name="Bot",
             prompt="(alias)", linked_to_channel="whatsapp"))
    db.commit(); db.close()

    assert _pode(channel="telegram") is False


# ── A chave por canal ──

def test_pausar_no_whatsapp_nao_cala_o_telegram_do_mesmo_numero(banco):
    """
    REGRESSÃO — a chave antiga era `(location_id, phone)`, sem canal. Pausar um
    número num canal pausava o outro, sem nada no log.
    """
    from services import ai_gate

    ai_gate.definir(LOC, "whatsapp", "5547", enabled=False, motivo=ai_gate.HANDOFF)

    assert _pode(channel="whatsapp") is False
    assert _pode(channel="telegram") is True


# ── O prazo ──

def test_pausa_com_prazo_vence_sozinha(banco):
    """
    REGRESSÃO — handoff descreve um evento em curso, não um decreto. Sem prazo, o
    lead que volta três semanas depois nunca mais é atendido por ninguém.
    """
    from services import ai_gate

    ai_gate.definir(LOC, "whatsapp", "5547", enabled=False,
                    motivo=ai_gate.HANDOFF, minutos=60)
    assert _pode() is False

    db = banco()
    linha = db.query(ConversationAIState).first()
    linha.until = datetime.utcnow() - timedelta(minutes=1)   # o prazo passou
    db.commit(); db.close()

    assert _pode() is True, "a pausa venceu e a IA continuou muda"


def test_pausa_SEM_prazo_nao_vence(banco):
    """`until=None` é 'até alguém religar' — e tem que continuar sendo."""
    from services import ai_gate

    ai_gate.definir(LOC, "whatsapp", "5547", enabled=False, motivo=ai_gate.QUALIFICADO)
    assert _pode() is False


def test_religar_zera_o_prazo(banco):
    """
    Senão uma pausa vencida ressuscita: a linha volta a `enabled=False` no dia em
    que alguém reusar o registro, com um `until` do passado que já não significa nada.
    """
    from services import ai_gate

    ai_gate.definir(LOC, "whatsapp", "5547", enabled=False,
                    motivo=ai_gate.HANDOFF, minutos=60)
    ai_gate.definir(LOC, "whatsapp", "5547", enabled=True,
                    motivo=None, mudado_por="crm")

    db = banco()
    linha = db.query(ConversationAIState).first()
    assert linha.enabled is True
    assert linha.until is None
    db.close()
    assert _pode() is True


def test_prazo_so_existe_para_PAUSA(banco):
    """
    `until` responde "até quando fica calada". Numa linha ligada ele não significa
    nada — e uma linha ligada com prazo no passado é uma bomba: basta alguém pausar
    depois sem informar prazo para a pausa já nascer vencida.
    """
    from services import ai_gate

    ai_gate.definir(LOC, "whatsapp", "5547", enabled=True,
                    motivo=None, mudado_por="crm", minutos=60)

    db = banco()
    linha = db.query(ConversationAIState).first()
    assert linha.until is None, "gravou prazo numa conversa que está LIGADA"
    db.close()


def test_definir_duas_vezes_atualiza_a_MESMA_linha(banco):
    from services import ai_gate

    ai_gate.definir(LOC, "whatsapp", "5547", enabled=False, motivo=ai_gate.HANDOFF)
    ai_gate.definir(LOC, "whatsapp", "5547", enabled=False, motivo=ai_gate.OPERADOR)

    db = banco()
    assert db.query(ConversationAIState).count() == 1
    assert db.query(ConversationAIState).first().motivo == ai_gate.OPERADOR
    db.close()


# ── A ordem das checagens ──

def test_o_portao_do_CRM_so_e_consultado_por_ULTIMO(banco):
    """
    Ele faz duas chamadas HTTP num client de timeout 30s, por turno, num pipeline
    cuja janela de debounce é 1,5s — e é fail-closed. Consultá-lo quando o estado
    local já negou transforma instabilidade do CRM em silêncio da IA sem motivo.
    """
    from services import ai_gate

    tocou = {"n": 0}

    async def _crm(location_id, contact_id):
        tocou["n"] += 1
        return True

    import services.ghl_service as ghlmod
    ghlmod.ghl_service.is_ai_active_for_contact = _crm

    ai_gate.definir(LOC, "whatsapp", "5547", enabled=False, motivo=ai_gate.HANDOFF)
    tenant_crm = SimpleNamespace(mode="ghl")

    assert _pode(tenant=tenant_crm, contact_id="c1") is False
    assert tocou["n"] == 0, "consultou o CRM mesmo com a conversa já pausada localmente"

    ai_gate.definir(LOC, "whatsapp", "5547", enabled=True)
    assert _pode(tenant=tenant_crm, contact_id="c1") is True
    assert tocou["n"] == 1, "o portão do CRM deixou de ser consultado quando devia"


def test_sem_CRM_nao_consulta_a_rede(banco):
    """`whatsapp_only` é o modo do onboarding: não pode depender de CRM nenhum."""
    from services import ai_gate

    async def _explode(*a, **k):
        raise AssertionError("consultou o CRM num tenant sem CRM")

    import services.ghl_service as ghlmod
    ghlmod.ghl_service.is_ai_active_for_contact = _explode

    ai_gate.definir(LOC, "whatsapp", "5547", enabled=True)
    assert _pode() is True


def test_modo_CRM_sem_contato_resolvido_nao_consulta(banco):
    """Sem `contact_id` não há o que perguntar — e perguntar seria fail-closed à toa."""
    import services.ghl_service as ghlmod

    async def _explode(*a, **k):
        raise AssertionError("consultou o CRM sem contato resolvido")

    ghlmod.ghl_service.is_ai_active_for_contact = _explode
    assert _pode(tenant=SimpleNamespace(mode="ghl"), contact_id=None) is True


# ── A janela do debounce ──

def test_pausar_DURANTE_a_espera_aborta_a_resposta(banco):
    """
    O gate roda quando a mensagem chega; a task então dorme ~1,5s antes de gerar.
    Nesse intervalo o operador assume a conversa — e sem esta segunda leitura a
    task acordaria e responderia por cima dele.

    A propriedade existia por acaso (o portão que morava no `ai_service` rodava
    depois do sono). Ao trazer a decisão para a borda, ela se perdeu.
    """
    from services import ai_gate

    assert _pode() is True                                    # entrada liberou
    ai_gate.definir(LOC, "whatsapp", "5547", enabled=False,   # operador assumiu
                    motivo=ai_gate.OPERADOR, mudado_por="operador")

    assert asyncio.run(
        ai_gate.pausado_durante_a_espera(LOC, "whatsapp", "5547")
    ) is True


def test_conversa_livre_nao_e_abortada_na_segunda_leitura(banco):
    from services import ai_gate

    assert asyncio.run(
        ai_gate.pausado_durante_a_espera(LOC, "whatsapp", "5547")
    ) is False


def test_banco_fora_na_RELEITURA_nao_engole_a_resposta(banco, monkeypatch):
    """
    FAIL-OPEN aqui, ao contrário do gate de entrada — e de propósito.

    A entrada já autorizou 1,5s atrás. Engolir a resposta porque o banco piscou
    deixa o lead sem retorno para se proteger de uma corrida que quase nunca
    acontece. O gate de entrada é que decide; este é segunda opinião.
    """
    import data.database as dbmod
    from services import ai_gate

    def _quebrado():
        raise RuntimeError("banco fora")

    monkeypatch.setattr(dbmod, "SessionLocal", _quebrado, raising=True)
    assert asyncio.run(
        ai_gate.pausado_durante_a_espera(LOC, "whatsapp", "5547")
    ) is False


# ── Falhar sem derrubar ──

# ── Religar: o caminho do operador ──

def test_religar_vale_para_TODOS_os_canais_da_conversa(banco):
    """
    REGRESSÃO que este conserto quase criou: até aqui, religar a IA era efeito
    colateral de APAGAR a qualificação — era ela o gate. Com o interruptor em
    tabela própria, apagar a qualificação sozinho deixaria a conversa muda para
    sempre, e esse era o único botão que o operador tinha.

    E vale por CONTATO, não por canal: quem clica "religar" está falando da
    pessoa, não do WhatsApp dela.
    """
    from services import ai_gate

    ai_gate.definir(LOC, "whatsapp", "5547", enabled=False,
                    motivo=ai_gate.HANDOFF, minutos=60)
    ai_gate.definir(LOC, "telegram", "5547", enabled=False, motivo=ai_gate.QUALIFICADO)

    assert ai_gate.religar_todos_os_canais(LOC, "5547") == 2
    assert _pode(channel="whatsapp") is True
    assert _pode(channel="telegram") is True

    db = banco()
    for linha in db.query(ConversationAIState).all():
        assert linha.until is None, "religou mas deixou o prazo — a pausa ressuscita"
        assert linha.motivo is None
    db.close()


def test_religar_nao_mexe_em_OUTRO_contato(banco):
    from services import ai_gate

    ai_gate.definir(LOC, "whatsapp", "5547", enabled=False, motivo=ai_gate.HANDOFF)
    ai_gate.definir(LOC, "whatsapp", "5548", enabled=False, motivo=ai_gate.HANDOFF)

    assert ai_gate.religar_todos_os_canais(LOC, "5547") == 1
    assert _pode(contato="5548") is False


def test_religar_quem_ja_esta_ligado_e_no_op(banco):
    """Não pode contar como 'religou 3' e nem reescrever linha à toa."""
    from services import ai_gate

    assert ai_gate.religar_todos_os_canais(LOC, "5547") == 0


def test_falha_ao_gravar_o_estado_nao_levanta(banco, monkeypatch):
    """
    Quem chama `definir` é o handoff ou a qualificação — trabalho já feito e já
    pago. Falhar ao gravar o interruptor não pode desfazer aquilo.
    """
    import data.database as dbmod
    from services import ai_gate

    class _Quebrado:
        def __call__(self):
            raise RuntimeError("banco fora")

    monkeypatch.setattr(dbmod, "SessionLocal", _Quebrado(), raising=True)
    assert ai_gate.definir(LOC, "whatsapp", "5547", enabled=False) is False
