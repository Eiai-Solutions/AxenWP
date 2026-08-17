"""
Entrevista pelo link público: do primeiro "oi" até virar OnboardingSubmission.

A rota é ANÔNIMA e cada turno gasta a nossa chave Anthropic, então os testes que
mais importam aqui não são os do caminho feliz — são os do escopo (token de um
tenant não continua entrevista de outro) e os do teto de gasto.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import services.master_interview as mi
from data.models import AgentInterview, Base, OnboardingSubmission, Tenant

TOKEN_A = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
TOKEN_B = "f0e9d8c7b6a5948372615f4e3d2c1b0a"


# ── Fakes da API Anthropic (mesmo padrão de test_master_interview) ──

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
    "company_name": "Pizzaria do Zé",
    "products_services": "pizza, esfiha e refri",
    "agent_goal": "tirar duvida e anotar pedido",
}


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    from data.database import Base as B  # noqa: F401
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
    db.add(Tenant(location_id="loc_b", company_name="Cliente B", form_token=TOKEN_B))
    db.commit()
    db.close()

    # `public.onboarding` abre a própria sessão para resolver o tenant.
    import public.onboarding as onb
    monkeypatch.setattr(onb, "SessionLocal", Session, raising=True)

    import main
    return SimpleNamespace(cliente=TestClient(main.app, raise_server_exceptions=False), Session=Session)


def _roteirizar(monkeypatch, roteiro):
    # UMA instância para todas as chamadas: `avancar` constrói um AsyncAnthropic por
    # turno, e um fake novo a cada vez reiniciaria o roteiro do zero.
    import anthropic
    cliente = FakeClient(roteiro)
    monkeypatch.setattr(anthropic, "AsyncAnthropic", lambda **kw: cliente, raising=True)
    return cliente


def _turno(cliente, form_token, token="", mensagem=""):
    return cliente.post(
        f"/form/{form_token}/entrevista/mensagem",
        json={"token": token, "mensagem": mensagem},
    )


# ── Caminho feliz ──

def test_abrir_a_entrevista_devolve_pergunta_e_token(ambiente, monkeypatch):
    _roteirizar(monkeypatch, [FakeResp([_texto("Oi! O que a sua empresa faz?")])])

    r = _turno(ambiente.cliente, TOKEN_A)
    d = r.json()

    assert r.status_code == 200 and d["success"] is True
    assert d["token"], "sem token o browser nao consegue continuar a conversa"
    assert d["concluida"] is False
    assert [m["de"] for m in d["historico"]] == ["mestre"]


def test_entrevista_concluida_vira_submission(ambiente, monkeypatch):
    """É isto que faz a entrevista desembocar no fluxo que já existe."""
    _roteirizar(monkeypatch, [
        FakeResp([_texto("O que voces vendem?")]),
        FakeResp([_tool("t1", "concluir_entrevista", COMPLETO)], stop_reason="tool_use"),
    ])

    token = _turno(ambiente.cliente, TOKEN_A).json()["token"]
    d = _turno(ambiente.cliente, TOKEN_A, token, "vendo pizza").json()

    assert d["concluida"] is True
    assert d["submission_id"], "nao criou a submission"

    db = ambiente.Session()
    sub = db.query(OnboardingSubmission).filter_by(id=d["submission_id"]).first()
    assert sub is not None
    assert sub.tenant_location_id == "loc_a"
    assert sub.status == "pending", "deve aguardar decisao do operador, como o form"
    assert sub.form_data["company_name"] == "Pizzaria do Zé"
    db.close()


def test_conversa_continua_de_onde_parou(ambiente, monkeypatch):
    """Fechar a aba e voltar não pode recomeçar (nem gastar tokens de novo)."""
    _roteirizar(monkeypatch, [
        FakeResp([_texto("Pergunta 1?")]),
        FakeResp([_texto("Pergunta 2?")]),
    ])

    token = _turno(ambiente.cliente, TOKEN_A).json()["token"]
    d = _turno(ambiente.cliente, TOKEN_A, token, "resposta 1").json()

    assert [m["de"] for m in d["historico"]] == ["mestre", "usuario", "mestre"]
    assert d["turnos"] == 2


# ── Escopo: o risco de privacidade desta rota ──

def test_token_de_um_cliente_nao_continua_entrevista_de_outro(ambiente, monkeypatch):
    """
    O link é público e o token é adivinhável por quem o recebeu. Sem esta barreira,
    o cliente B leria a entrevista do cliente A só trocando a URL.
    """
    _roteirizar(monkeypatch, [FakeResp([_texto("Oi!")])])
    token_a = _turno(ambiente.cliente, TOKEN_A).json()["token"]

    r = _turno(ambiente.cliente, TOKEN_B, token_a, "quero ver")

    assert r.status_code == 404
    assert r.json()["success"] is False


def test_link_inexistente_e_recusado(ambiente, monkeypatch):
    _roteirizar(monkeypatch, [FakeResp([_texto("Oi!")])])
    assert _turno(ambiente.cliente, "0000000000000000000000000000dead").status_code == 404


def test_token_de_sessao_desconhecido_e_recusado(ambiente, monkeypatch):
    _roteirizar(monkeypatch, [FakeResp([_texto("Oi!")])])
    assert _turno(ambiente.cliente, TOKEN_A, "sessao-inventada", "oi").status_code == 404


# ── Teto de gasto (a rota é anônima e queima a nossa chave) ──

def test_teto_de_turnos_devolve_429_em_vez_de_gastar(ambiente, monkeypatch):
    _roteirizar(monkeypatch, [FakeResp([_texto("Oi!")])])
    token = _turno(ambiente.cliente, TOKEN_A).json()["token"]

    db = ambiente.Session()
    linha = db.query(AgentInterview).filter_by(token=token).first()
    estado = mi.EstadoEntrevista.from_json(linha.estado)
    estado.turnos = mi.MAX_TURNOS
    linha.estado = estado.to_json()
    db.commit()
    db.close()

    r = _turno(ambiente.cliente, TOKEN_A, token, "mais uma")
    assert r.status_code == 429
    assert "limite" in r.json()["error"].lower()


def test_teto_estourado_no_meio_do_turno_persiste_o_que_ja_foi_gasto(ambiente, monkeypatch):
    """
    REGRESSÃO — o freio de gasto nunca fechava.

    Quando o teto estoura DENTRO do loop, `avancar` levanta e o estado nunca era
    salvo: o banco voltava ao valor anterior e cada nova tentativa refazia as
    MESMAS buscas, que a Anthropic já tinha cobrado. Num link público e anônimo,
    isso é conta aberta.
    """
    _roteirizar(monkeypatch, [FakeResp([_texto("Oi!")])])
    token = _turno(ambiente.cliente, TOKEN_A).json()["token"]

    # A resposta seguinte consome buscas e estoura o teto no meio do turno.
    caro = FakeResp([_texto("procurando...")], stop_reason="pause_turn")
    caro.usage = SimpleNamespace(
        input_tokens=100, output_tokens=50, cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
        server_tool_use=SimpleNamespace(web_search_requests=mi.MAX_BUSCAS_WEB),
    )
    _roteirizar(monkeypatch, [caro])

    r = _turno(ambiente.cliente, TOKEN_A, token, "acha minha empresa")
    assert r.status_code == 429
    assert "pesquisas" in r.json()["error"].lower(), "mensagem culpa o teto errado"

    db = ambiente.Session()
    estado = mi.EstadoEntrevista.from_json(
        db.query(AgentInterview).filter_by(token=token).first().estado
    )
    db.close()
    assert estado.buscas_web >= mi.MAX_BUSCAS_WEB, \
        "as buscas pagas sumiram do banco — a proxima tentativa paga de novo"


def test_falha_da_mestre_nao_vaza_detalhe_interno(ambiente, monkeypatch):
    """Erro de infra não pode virar stack trace na tela de um anônimo."""
    import anthropic

    class Explode:
        def __init__(self, **kw):
            self.messages = self

        async def create(self, **kw):
            raise RuntimeError("api_key sk-abc123 invalida")

    monkeypatch.setattr(anthropic, "AsyncAnthropic", Explode, raising=True)

    r = _turno(ambiente.cliente, TOKEN_A)
    assert r.status_code == 502
    corpo = r.json()
    assert corpo["success"] is False
    assert "sk-abc123" not in str(corpo), "vazou credencial na resposta"


# ── A tela ──

def test_pagina_da_entrevista_abre(ambiente):
    r = ambiente.cliente.get(f"/form/{TOKEN_A}/entrevista")
    assert r.status_code == 200
    assert "Cliente A" in r.text
    assert "Usar formulário" in r.text, "sem a saida para o formulario, quem odeia chat trava"
    # Sem recomeçar, o token preso no localStorage faz o link devolver a conversa
    # antiga PARA SEMPRE — foi o que o Luiz encontrou usando.
    assert "function recomecar()" in r.text
    assert "localStorage.removeItem(CHAVE)" in r.text, "recomeçar sem limpar o token nao recomeca nada"


def test_pagina_com_link_invalido_nao_abre(ambiente):
    assert ambiente.cliente.get("/form/0000000000000000000000000000dead/entrevista").status_code == 404
