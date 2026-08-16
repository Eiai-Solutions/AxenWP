"""
REPRO: a porta "recomendado" do wizard (Conversar com a Mestre) nao devolve nada
ao rascunho.

Percurso exato do operador:
  1. abre o wizard  -> rascunho
  2. clica "Conversar com a Mestre" -> a tela faz SO isto: salvar({origem: porta})
     + window.open(link/entrevista)   (dashboard.js:891-894)
  3. o cliente conduz a entrevista inteira no link publico ate concluir
  4. o operador volta ao wizard e tenta publicar
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import services.master_interview as mi
from data.models import AdminUser, AgentDraft, Base, OnboardingSubmission, Tenant

LOC_A = "wp_aaaaaaaaaaaa"
FORM_TOKEN = "a1b2c3d4e5f60718293a4b5c6d7e8f90"


def _texto(t):
    return SimpleNamespace(type="text", text=t, model_dump=lambda: {"type": "text", "text": t})


def _tool(bid, nome, entrada):
    return SimpleNamespace(
        type="tool_use", id=bid, name=nome, input=entrada,
        model_dump=lambda: {"type": "tool_use", "id": bid, "name": nome, "input": entrada},
    )


def _uso():
    return SimpleNamespace(input_tokens=500, output_tokens=50,
                           cache_read_input_tokens=0, cache_creation_input_tokens=0)


class FakeResp:
    def __init__(self, content, stop_reason="end_turn"):
        self.content, self.stop_reason, self.usage = content, stop_reason, _uso()


class FakeClient:
    def __init__(self, roteiro):
        self._roteiro, self.messages = list(roteiro), self

    async def create(self, **kw):
        return self._roteiro.pop(0) if self._roteiro else FakeResp([_texto("...")])


COMPLETO = {
    "company_name": "Pizzaria do Ze",
    "products_services": "pizza, esfiha e refri",
    "agent_goal": "tirar duvida e anotar pedido",
}


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    import data.models  # noqa: F401
    import services.admin_auth as auth
    import services.draft_service as ds
    import services.interview_session as sess
    import public.onboarding as onb
    import data.database as dbmod
    from utils.config import settings
    from utils.limiter import limiter

    engine = create_engine(f"sqlite:///{tmp_path}/porta.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    for mod in (ds, sess, onb, auth, dbmod):
        monkeypatch.setattr(mod, "SessionLocal", Session, raising=True)
    monkeypatch.setattr(ds, "_tem_chave_do_motor", lambda: True, raising=True)
    monkeypatch.setattr(settings, "debug", True, raising=False)
    monkeypatch.setattr(limiter, "enabled", False, raising=False)
    monkeypatch.setattr(mi, "_resolve_master_key", lambda: "sk-fake", raising=True)
    monkeypatch.setattr(mi, "_read_settings", lambda: ("anthropic", None), raising=True)

    db = Session()
    db.add(Tenant(location_id=LOC_A, company_name="Cliente A", mode="ghl", pit_token="pit",
                  form_token=FORM_TOKEN,
                  whatsapp_provider="waha", waha_base_url="https://w", waha_session="s",
                  waha_api_key="k"))
    db.add(AdminUser(username="op", password_hash=auth.hash_password("senha-operador"),
                     is_active=True, role=auth.OPERADOR))
    db.commit()
    u = db.query(AdminUser).filter_by(username="op").first()
    cookie = auth.make_session_value(u.username, u.password_hash)
    db.close()

    import main
    return SimpleNamespace(c=TestClient(main.app, raise_server_exceptions=False),
                           cookie=cookie, Session=Session)


def _roteirizar(monkeypatch, roteiro):
    import anthropic
    cliente = FakeClient(roteiro)
    monkeypatch.setattr(anthropic, "AsyncAnthropic", lambda **kw: cliente, raising=True)
    return cliente


def test_porta_entrevista_nao_devolve_nada_ao_rascunho(ambiente, monkeypatch):
    ck = {"admin_session": ambiente.cookie}
    c = ambiente.c

    # 1. Operador abre o wizard.
    d0 = c.post(f"/admin/agents/{LOC_A}/wizard/abrir", cookies=ck).json()
    assert d0["success"] is True
    draft_id = d0["rascunho"]["id"]

    # 2. Clica na porta recomendada. E ISTO que dashboard.js:888-894 faz:
    #    gera o link + salva SO {origem} + abre a aba.
    link = c.post(f"/admin/tenant/{LOC_A}/generate-form-link", cookies=ck).json()
    print("link:", link)
    r = c.post(f"/admin/agents/{LOC_A}/wizard/{draft_id}/etapa",
               json={"origem": "entrevista"}, cookies=ck).json()
    assert r["success"] is True

    # 3. O cliente conduz a entrevista INTEIRA no link publico ate concluir.
    _roteirizar(monkeypatch, [
        FakeResp([_texto("O que voces vendem?")]),
        FakeResp([_tool("t1", "concluir_entrevista", COMPLETO)], stop_reason="tool_use"),
    ])
    tok = c.post(f"/form/{FORM_TOKEN}/entrevista/mensagem", json={"token": "", "mensagem": ""}).json()["token"]
    fim = c.post(f"/form/{FORM_TOKEN}/entrevista/mensagem",
                 json={"token": tok, "mensagem": "vendo pizza"}).json()
    print("entrevista concluida:", fim["concluida"], "| submission:", fim["submission_id"])
    assert fim["concluida"] is True and fim["submission_id"]

    db = ambiente.Session()
    assert db.query(OnboardingSubmission).count() == 1
    db.close()

    # 4. Operador volta ao wizard.
    est = c.get(f"/admin/agents/{LOC_A}/wizard/{draft_id}", cookies=ck).json()
    rasc = est["rascunho"]
    print("rascunho apos a entrevista:", {k: rasc[k] for k in
          ("origem", "interview_token", "submission_id", "prompt", "spec")})
    print("pode_publicar:", est["pode_publicar"], "| impedimento:", est["impedimento"])

    assert rasc["origem"] == "entrevista"
    assert rasc["interview_token"] is None
    assert rasc["submission_id"] is None
    assert rasc["prompt"] is None
    assert rasc["spec"] is None
    assert est["pode_publicar"] is False
    assert "prompt" in (est["impedimento"] or "")

    # 5. Publicar e recusado, e nao ha na tela nenhum controle que importe o
    #    resultado da entrevista.
    pub = c.post(f"/admin/agents/{LOC_A}/wizard/{draft_id}/publicar", cookies=ck).json()
    print("publicar:", pub)
    assert pub["success"] is False

    db = ambiente.Session()
    assert db.query(AgentDraft).filter_by(id=draft_id).first().status == "rascunho"
    db.close()
