"""
Fiação PR2: prompt for_tools, derivação de ações das tools, handler de escalação.
"""

from types import SimpleNamespace

import pytest

from services.agent_engine.base import ToolCall
from services.agent_engine.tools import ESCALATE, QUALIFY


FIELDS = [{"key": "nome", "label": "Nome"}, {"key": "empresa", "label": "Empresa"}]


# ── Prompt no modo tools ──

def test_prompt_for_tools_usa_tool_nao_marcador():
    from services.prompt_builder import build_system_prompt

    p = build_system_prompt("Você é SDR.", qualification_enabled=True,
                            qualification_fields=FIELDS, for_tools=True)
    assert "register_qualified_lead" in p
    assert "[QUALIFIED_DATA]" not in p       # sem marcador de texto
    assert "escalate_to_human" in p
    assert "Nome" in p and "Empresa" in p


def test_prompt_langchain_mantem_marcador():
    from services.prompt_builder import build_system_prompt

    p = build_system_prompt("Você é SDR.", qualification_enabled=True,
                            qualification_fields=FIELDS, for_tools=False)
    assert "[QUALIFIED_DATA]" in p           # comportamento legado intacto
    assert "register_qualified_lead" not in p


def test_prompt_for_tools_sem_qualificacao_ainda_tem_escalar():
    from services.prompt_builder import build_system_prompt

    p = build_system_prompt("Você é SDR.", qualification_enabled=False, for_tools=True)
    assert "escalate_to_human" in p


# ── Derivação de ações das tool_calls ──

def test_extrai_qualificacao_da_tool():
    from services.ai_service import AIEngine

    calls = [ToolCall(name=QUALIFY, arguments={"nome": "Luiz", "empresa": "Eiai"}, result={"status": "ok"})]
    qual, handoff = AIEngine._extract_tool_actions(calls)
    assert qual == {"nome": "Luiz", "empresa": "Eiai"}
    assert handoff is None


def test_extrai_handoff_da_tool():
    from services.ai_service import AIEngine

    calls = [ToolCall(name=ESCALATE, arguments={"motivo": "pediu humano"}, result={"status": "ok"})]
    qual, handoff = AIEngine._extract_tool_actions(calls)
    assert qual is None
    assert handoff == {"reason": "pediu humano"}


def test_sem_tool_nao_deriva_nada():
    from services.ai_service import AIEngine

    qual, handoff = AIEngine._extract_tool_actions([])
    assert qual is None and handoff is None


# ── Guard de completude (paridade com o LangChain) ──

def _engine_claude_fake(monkeypatch, fields):
    """AIEngine mínimo para testar métodos, sem construir LLM/cliente real."""
    from services import ai_service

    agent = SimpleNamespace(
        location_id="loc1", channel="whatsapp", is_active=True, api_key=None,
        agent_engine="claude", anthropic_api_key=None, anthropic_model=None,
        model="x", prompt="p", qualification_enabled=True, qualification_fields=fields,
        form_data={}, name="a",
    )
    # Não deixa o __init__ tentar construir cliente Anthropic real.
    monkeypatch.setattr(ai_service.AIEngine, "_build_claude_engine", lambda self, a: object())
    return ai_service.AIEngine(agent)


def test_qualificacao_incompleta_nao_qualifica(monkeypatch):
    fields = [{"key": "nome", "label": "Nome"}, {"key": "email", "label": "Email"}]
    eng = _engine_claude_fake(monkeypatch, fields)
    assert eng._qualification_complete({"nome": "Ana", "email": "a@b.com"}) is True
    assert eng._qualification_complete({"nome": "Ana", "email": ""}) is False   # vazio não conta
    assert eng._qualification_complete({"nome": "Ana"}) is False                 # faltando
    assert eng._qualification_complete({}) is False


def test_campo_auto_nao_exigido_na_completude(monkeypatch):
    fields = [{"key": "nome", "label": "Nome"}, {"key": "temp", "label": "Temp", "auto": True}]
    eng = _engine_claude_fake(monkeypatch, fields)
    # só 'nome' é de coleta; 'temp' (auto) não é exigido
    assert eng._qualification_complete({"nome": "Ana"}) is True


def test_agente_claude_sem_openrouter_ainda_tem_engine(monkeypatch):
    """O gate legado não pode barrar um agente claude só porque falta OpenRouter."""
    from services import ai_service

    agent = SimpleNamespace(
        location_id="loc1", channel="whatsapp", is_active=True, api_key=None,
        agent_engine="claude", anthropic_api_key="sk-ant-x", anthropic_model=None,
        model="x", prompt="p", qualification_enabled=False, qualification_fields=[],
        form_data={}, name="a",
    )
    sentinel = object()
    monkeypatch.setattr(ai_service.AIEngine, "_build_claude_engine", lambda self, a: sentinel)
    eng = ai_service.AIEngine(agent)
    assert eng.engine is sentinel          # motor claude construído
    assert eng.engine_name == "claude"     # não caiu para langchain


# ── Handler de escalação (kill-switch + nota) ──

@pytest.mark.asyncio
async def test_escalacao_ghl_pausa_ia_e_cria_nota(monkeypatch):
    from services import escalation_handler as eh

    chamadas = {"pause": None, "note": None}

    async def _field_id(loc, name):
        return "fld_status_ia"

    async def _update(loc, cid, data):
        chamadas["pause"] = (cid, data)
        return {}

    async def _note(loc, cid, body):
        chamadas["note"] = (cid, body)
        return True

    monkeypatch.setattr(eh.ghl_service, "_get_custom_field_id_by_name", _field_id)
    monkeypatch.setattr(eh.ghl_service, "update_contact", _update)
    monkeypatch.setattr(eh.ghl_service, "create_contact_note", _note)

    tenant = SimpleNamespace(mode="ghl")
    await eh.handle_escalation("loc1", "5547", "C1", tenant, "pediu humano", "whatsapp")

    assert chamadas["pause"][0] == "C1"
    assert chamadas["pause"][1]["customFields"][0]["field_value"] == "Desativada"
    assert chamadas["note"][0] == "C1"
    assert "pediu humano" in chamadas["note"][1]


@pytest.mark.asyncio
async def test_escalacao_pausa_a_IA_sem_forjar_um_lead_qualificado(monkeypatch, tmp_path):
    """
    REGRESSÃO — o kill-switch era um lead qualificado FALSO.

    Para conseguir pausar, `handle_escalation` gravava um `QualifiedLead` com
    `qualified_data={"_handoff": True}`: quem só pediu um atendente entrava na
    tabela de qualificados, aparecia como "Qualificado" no painel e contava na
    métrica de leads. E religar a IA exigia APAGAR esse registro.

    Agora a pausa é estado próprio. Este teste guarda as duas metades: a pausa
    existe, E a tabela de negócio continua limpa.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import data.database as dbmod
    from data.models import Base, ConversationAIState, QualifiedLead
    from services import escalation_handler as eh

    engine = create_engine(f"sqlite:///{tmp_path}/q.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(dbmod, "SessionLocal", Session)

    tocou = {"crm": False}

    async def _boom(*a, **k):
        tocou["crm"] = True
        return None

    monkeypatch.setattr(eh.ghl_service, "_get_custom_field_id_by_name", _boom)
    monkeypatch.setattr(eh.ghl_service, "create_contact_note", _boom)

    tenant = SimpleNamespace(mode="whatsapp_only")
    await eh.handle_escalation("loc1", "5547", None, tenant, "pediu humano", "whatsapp")

    assert tocou["crm"] is False  # sem CRM não toca no GHL

    s = Session()
    try:
        assert s.query(QualifiedLead).count() == 0, (
            "voltou a forjar lead qualificado para conseguir pausar"
        )
        (estado,) = s.query(ConversationAIState).all()
        assert estado.enabled is False
        assert estado.motivo == "handoff"
        assert estado.channel == "whatsapp"
        assert estado.contact_ref == "5547"
        assert estado.until is not None, (
            "pausa sem prazo: o lead que voltar em três semanas nunca mais é atendido"
        )
    finally:
        s.close()


@pytest.mark.asyncio
async def test_escalar_no_whatsapp_nao_cala_o_telegram_do_mesmo_numero(monkeypatch, tmp_path):
    """
    REGRESSÃO — a chave antiga era `(location_id, phone)`, sem canal.

    Pausar um número no WhatsApp pausava o Telegram dele junto, sem nada no log.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import data.database as dbmod
    from data.models import Base
    from services import ai_gate, escalation_handler as eh

    engine = create_engine(f"sqlite:///{tmp_path}/q3.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(dbmod, "SessionLocal", Session)

    tenant = SimpleNamespace(mode="whatsapp_only")
    await eh.handle_escalation("loc1", "5547", None, tenant, "x", "whatsapp")

    assert ai_gate._estado_sync("loc1", "whatsapp", "5547")["enabled"] is False
    assert ai_gate._estado_sync("loc1", "telegram", "5547") is None, (
        "escalar num canal deixou estado no outro"
    )


@pytest.mark.asyncio
async def test_escalar_tres_vezes_nao_duplica_nem_estende_para_sempre(monkeypatch, tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import data.database as dbmod
    from data.models import Base, ConversationAIState
    from services import escalation_handler as eh

    engine = create_engine(f"sqlite:///{tmp_path}/q2.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(dbmod, "SessionLocal", Session)

    tenant = SimpleNamespace(mode="whatsapp_only")
    for _ in range(3):
        await eh.handle_escalation("loc1", "5547", None, tenant, "x", "whatsapp")

    s = Session()
    try:
        assert s.query(ConversationAIState).count() == 1  # não duplica
    finally:
        s.close()


@pytest.mark.asyncio
async def test_escalacao_falha_no_crm_nao_propaga(monkeypatch):
    """Best-effort: erro no GHL não pode derrubar o turno."""
    from services import escalation_handler as eh

    async def _field_id(loc, name):
        return "fld"

    async def _boom(*a, **k):
        raise RuntimeError("GHL fora")

    monkeypatch.setattr(eh.ghl_service, "_get_custom_field_id_by_name", _field_id)
    monkeypatch.setattr(eh.ghl_service, "update_contact", _boom)
    monkeypatch.setattr(eh.ghl_service, "create_contact_note", _boom)

    tenant = SimpleNamespace(mode="ghl")
    # não deve levantar
    await eh.handle_escalation("loc1", "5547", "C1", tenant, "x", "whatsapp")


@pytest.mark.asyncio
async def test_com_CRM_a_pausa_do_handoff_fica_NO_CRM_para_poder_ser_desfeita(monkeypatch, tmp_path):
    """
    REGRESSÃO que a revisão pegou antes de subir.

    No modo com CRM, o interruptor que o operador VÊ e MEXE é o campo "Status IA"
    do contato. Criar ALÉM dele uma pausa local de 24h tornaria o religar inócuo:
    o operador marcaria "Ativada" e a IA seguiria muda, sem nada no log dizendo
    por quê. A pausa local é o fallback de quando o CRM não pode segurar.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import data.database as dbmod
    from data.models import Base, ConversationAIState
    from services import escalation_handler as eh

    engine = create_engine(f"sqlite:///{tmp_path}/crm.db")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(dbmod, "SessionLocal", sessionmaker(bind=engine))

    escreveu = {}

    async def _field(loc, nome):
        return "fld_status_ia"

    async def _update(loc, cid, data):
        escreveu["campo"] = data
        return {"ok": True}

    async def _nota(*a, **k):
        return True

    monkeypatch.setattr(eh.ghl_service, "_get_custom_field_id_by_name", _field)
    monkeypatch.setattr(eh.ghl_service, "update_contact", _update)
    monkeypatch.setattr(eh.ghl_service, "create_contact_note", _nota)

    await eh.handle_escalation("loc1", "5547", "contato1",
                               SimpleNamespace(mode="ghl"), "pediu humano", "whatsapp")

    assert escreveu["campo"]["customFields"][0]["field_value"] == "Desativada"
    s = sessionmaker(bind=engine)()
    try:
        assert s.query(ConversationAIState).count() == 0, (
            "criou pausa local ALÉM do campo do CRM — religar pelo CRM deixaria de funcionar"
        )
    finally:
        s.close()


@pytest.mark.asyncio
async def test_se_o_CRM_nao_segura_a_pausa_o_hub_segura(monkeypatch, tmp_path):
    """O fallback: sem campo configurado, a promessa da tool só é verdade localmente."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import data.database as dbmod
    from data.models import Base, ConversationAIState
    from services import escalation_handler as eh

    engine = create_engine(f"sqlite:///{tmp_path}/crm2.db")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(dbmod, "SessionLocal", sessionmaker(bind=engine))

    async def _sem_campo(loc, nome):
        return None            # o tenant nunca criou o custom field

    async def _nota(*a, **k):
        return True

    monkeypatch.setattr(eh.ghl_service, "_get_custom_field_id_by_name", _sem_campo)
    monkeypatch.setattr(eh.ghl_service, "create_contact_note", _nota)

    await eh.handle_escalation("loc1", "5547", "contato1",
                               SimpleNamespace(mode="ghl"), "x", "whatsapp")

    s = sessionmaker(bind=engine)()
    try:
        (estado,) = s.query(ConversationAIState).all()
        assert estado.enabled is False and estado.motivo == "handoff"
    finally:
        s.close()
