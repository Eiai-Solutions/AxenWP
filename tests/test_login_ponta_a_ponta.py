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
