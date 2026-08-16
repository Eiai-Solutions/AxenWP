"""
REPRO: entrevista conclui, submission NAO nasce, e a tela diz "enviado".

Falha de banco de verdade no INSERT da submission (aqui: a tabela some, como
seria um erro de conexao/permissao/constraint no pooler em producao).
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import services.master_interview as mi
from data.models import AgentInterview, Base, OnboardingSubmission, Tenant

TOKEN_A = "a1b2c3d4e5f60718293a4b5c6d7e8f90"


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
    import services.interview_session as sess
    from utils.config import settings
    from utils.limiter import limiter

    engine = create_engine(f"sqlite:///{tmp_path}/ent.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    monkeypatch.setattr(sess, "SessionLocal", Session, raising=True)
    monkeypatch.setattr(settings, "debug", True, raising=False)
    monkeypatch.setattr(limiter, "enabled", False, raising=False)
    monkeypatch.setattr(mi, "_resolve_master_key", lambda: "sk-fake", raising=True)
    monkeypatch.setattr(mi, "_read_settings", lambda: ("anthropic", None), raising=True)

    db = Session()
    db.add(Tenant(location_id="loc_a", company_name="Cliente A", form_token=TOKEN_A))
    db.commit()
    db.close()

    import public.onboarding as onb
    monkeypatch.setattr(onb, "SessionLocal", Session, raising=True)

    import main
    return SimpleNamespace(
        cliente=TestClient(main.app, raise_server_exceptions=False),
        Session=Session, engine=engine,
    )


def _roteirizar(monkeypatch, roteiro):
    import anthropic
    cliente = FakeClient(roteiro)
    monkeypatch.setattr(anthropic, "AsyncAnthropic", lambda **kw: cliente, raising=True)
    return cliente


def _turno(cliente, form_token, token="", mensagem=""):
    return cliente.post(
        f"/form/{form_token}/entrevista/mensagem",
        json={"token": token, "mensagem": mensagem},
    )


def test_submission_falha_e_a_tela_ainda_declara_sucesso(ambiente, monkeypatch):
    _roteirizar(monkeypatch, [
        FakeResp([_texto("O que voces vendem?")]),
        FakeResp([_tool("t1", "concluir_entrevista", COMPLETO)], stop_reason="tool_use"),
    ])

    token = _turno(ambiente.cliente, TOKEN_A).json()["token"]

    # Falha de banco REAL no INSERT da submission (nao mexo na funcao sob teste).
    with ambiente.engine.begin() as c:
        c.execute(text("DROP TABLE onboarding_submissions"))

    r = _turno(ambiente.cliente, TOKEN_A, token, "vendo pizza")
    d = r.json()

    print("\n--- resposta da rota ---")
    print("status_code:", r.status_code)
    print("success:", d.get("success"))
    print("concluida:", d.get("concluida"))
    print("submission_id:", repr(d.get("submission_id")))

    # A rota diz SUCESSO com submission_id nulo.
    assert r.status_code == 200
    assert d["success"] is True
    assert d["concluida"] is True
    assert d["submission_id"] is None

    # A entrevista fica gravada como concluida -> nao ha retentativa possivel.
    db = ambiente.Session()
    linha = db.query(AgentInterview).filter_by(token=token).first()
    print("interview.status:", linha.status)
    print("interview.submission_id:", repr(linha.submission_id))
    assert linha.status == "concluded"
    assert linha.submission_id is None
    db.close()

    # Recarregar a pagina (turno(null)) nao tenta de novo: cai no early-return.
    Base.metadata.tables["onboarding_submissions"].create(ambiente.engine)  # banco "voltou"
    d2 = _turno(ambiente.cliente, TOKEN_A, token, "").json()
    print("--- apos recarregar com o banco de volta ---")
    print("concluida:", d2.get("concluida"), "| submission_id:", repr(d2.get("submission_id")))
    assert d2["concluida"] is True
    assert d2["submission_id"] is None

    db = ambiente.Session()
    n = db.query(OnboardingSubmission).count()
    print("submissions no banco:", n)
    assert n == 0, "nenhuma submission jamais nasce"
    db.close()
