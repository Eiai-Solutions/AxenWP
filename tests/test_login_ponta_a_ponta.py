"""
Fluxo HTTP completo do login: bootstrap -> POST /admin/login -> cookie -> painel.

Existe por um motivo específico: trocar a senha compartilhada por contas de
usuário mexe no caminho que dá acesso ao painel de PRODUÇÃO. Testes de unidade
nas funções de hash não provam que o operador consegue entrar — este prova.
Se este teste cair, o deploy tranca o Luiz para fora do próprio painel.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def app_com_operador(tmp_path, monkeypatch):
    from data.database import Base
    import data.models  # noqa: F401
    import services.admin_auth as auth
    from utils.config import settings

    engine = create_engine(
        f"sqlite:///{tmp_path/'login.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(auth, "SessionLocal", Session, raising=True)

    monkeypatch.setattr(settings, "admin_user", "luiz", raising=False)
    monkeypatch.setattr(settings, "admin_password", "senha-boa-do-env", raising=False)
    monkeypatch.setattr(settings, "debug", True, raising=False)  # cookie sem `secure` no TestClient
    auth.bootstrap_admin_user()

    import main

    return TestClient(main.app, raise_server_exceptions=False)


def test_o_operador_consegue_entrar_e_o_cookie_abre_o_painel(app_com_operador):
    c = app_com_operador
    r = c.post(
        "/admin/login",
        data={"username": "luiz", "password": "senha-boa-do-env"},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    assert r.headers["location"] == "/admin/dashboard"

    cookie = r.cookies.get("admin_session")
    assert cookie and cookie.startswith("luiz:"), f"cookie inesperado: {cookie!r}"

    # O cookie realmente abre uma rota protegida (não só o dashboard).
    r2 = c.get("/admin/agents/onboarding/submissions", cookies={"admin_session": cookie})
    assert r2.status_code != 401, "cookie válido foi recusado por rota protegida"


def test_senha_errada_volta_para_o_login_sem_cookie(app_com_operador):
    r = app_com_operador.post(
        "/admin/login",
        data={"username": "luiz", "password": "errada"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "/admin/login?error=" in r.headers["location"]
    assert not r.cookies.get("admin_session")


def test_usuario_errado_com_senha_certa_nao_entra(app_com_operador):
    r = app_com_operador.post(
        "/admin/login",
        data={"username": "outro", "password": "senha-boa-do-env"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "/admin/login?error=" in r.headers["location"]
    assert not r.cookies.get("admin_session")


def test_a_mensagem_de_erro_nao_diz_se_o_usuario_existe(app_com_operador):
    """Mensagens diferentes entregariam quais contas existem."""
    c = app_com_operador
    a = c.post("/admin/login", data={"username": "luiz", "password": "x"}, follow_redirects=False)
    b = c.post("/admin/login", data={"username": "fantasma", "password": "x"}, follow_redirects=False)
    assert a.headers["location"] == b.headers["location"]


def test_sem_cookie_a_rota_protegida_recusa(app_com_operador):
    r = app_com_operador.get("/admin/agents/onboarding/submissions")
    assert r.status_code == 401


def test_cookie_forjado_nao_abre_o_painel(app_com_operador):
    r = app_com_operador.get(
        "/admin/agents/onboarding/submissions",
        cookies={"admin_session": "luiz:" + "0" * 64},
    )
    assert r.status_code == 401


def test_username_com_dois_pontos_ainda_abre_o_painel(app_com_operador, monkeypatch):
    """
    Regressão pega em revisão: com `partition(":")` o cookie de um usuário cujo
    nome contém ":" gerava login 303 bem-sucedido que NUNCA abria o painel —
    trancamento silencioso, sem erro na tela nem log de recusa.
    """
    import services.admin_auth as auth
    from data.models import AdminUser

    db = auth.SessionLocal()
    db.add(AdminUser(username="luiz:ops", password_hash=auth.hash_password("s3nh4-boa"), is_active=True))
    db.commit()
    db.close()

    r = app_com_operador.post(
        "/admin/login", data={"username": "luiz:ops", "password": "s3nh4-boa"}, follow_redirects=False
    )
    assert r.status_code == 303
    cookie = r.cookies.get("admin_session")
    assert cookie, "login nao emitiu cookie"
    assert auth.resolve_session(cookie) == "luiz:ops", "cookie emitido nao valida — trancamento"


def test_troca_de_senha_exige_a_senha_atual(app_com_operador):
    """Cookie roubado não pode virar troca de senha e trancar o dono para fora."""
    c = app_com_operador
    login = c.post(
        "/admin/login",
        data={"username": "luiz", "password": "senha-boa-do-env"},
        follow_redirects=False,
    )
    cookie = login.cookies.get("admin_session")

    r = c.post(
        "/admin/senha",
        data={"senha_atual": "chute-errado", "senha_nova": "nova-senha-longa"},
        cookies={"admin_session": cookie},
        follow_redirects=False,
    )
    assert "err=" in r.headers["location"]

    from services.admin_auth import authenticate
    assert authenticate("luiz", "senha-boa-do-env") is not None, "senha foi trocada sem confirmar"


def test_troca_de_senha_funciona_e_mantem_quem_trocou_logado(app_com_operador):
    c = app_com_operador
    login = c.post(
        "/admin/login",
        data={"username": "luiz", "password": "senha-boa-do-env"},
        follow_redirects=False,
    )
    antigo = login.cookies.get("admin_session")

    r = c.post(
        "/admin/senha",
        data={"senha_atual": "senha-boa-do-env", "senha_nova": "outra-senha-longa"},
        cookies={"admin_session": antigo},
        follow_redirects=False,
    )
    assert "msg=" in r.headers["location"], r.headers.get("location")

    from services.admin_auth import authenticate, resolve_session
    assert authenticate("luiz", "outra-senha-longa") is not None
    assert authenticate("luiz", "senha-boa-do-env") is None
    # a sessao antiga morre; a reemitida continua valendo
    assert resolve_session(antigo) is None, "sessao antiga sobreviveu a troca"
    assert resolve_session(r.cookies.get("admin_session")) == "luiz"


def test_senha_nova_curta_e_recusada(app_com_operador):
    c = app_com_operador
    login = c.post(
        "/admin/login",
        data={"username": "luiz", "password": "senha-boa-do-env"},
        follow_redirects=False,
    )
    r = c.post(
        "/admin/senha",
        data={"senha_atual": "senha-boa-do-env", "senha_nova": "curta"},
        cookies={"admin_session": login.cookies.get("admin_session")},
        follow_redirects=False,
    )
    assert "err=" in r.headers["location"]
    from services.admin_auth import authenticate
    assert authenticate("luiz", "senha-boa-do-env") is not None


def test_troca_de_senha_sem_sessao_nao_passa(app_com_operador):
    r = app_com_operador.post(
        "/admin/senha",
        data={"senha_atual": "senha-boa-do-env", "senha_nova": "outra-senha-longa"},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/admin/login"
