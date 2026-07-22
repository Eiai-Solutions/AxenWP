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
async def test_escalacao_whatsapp_only_pausa_via_qualified_lead(monkeypatch, tmp_path):
    """Sem CRM, o kill-switch durável é uma linha em QualifiedLead (o gate da IA)."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import data.database as dbmod
    from data.models import Base, QualifiedLead
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
        rows = s.query(QualifiedLead).all()
        assert len(rows) == 1  # pausa durável persistida
        assert rows[0].qualified_data.get("_handoff") is True
    finally:
        s.close()


@pytest.mark.asyncio
async def test_escalacao_whatsapp_only_idempotente(monkeypatch, tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import data.database as dbmod
    from data.models import Base, QualifiedLead
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
        assert s.query(QualifiedLead).count() == 1  # não duplica
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
