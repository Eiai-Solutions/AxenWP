"""
`/admin` é área da EQUIPE. Um usuário com papel de cliente não entra em nada dali.

Por que este arquivo existe: até agora `verify_admin` perguntava apenas "o cookie
resolve para algum AdminUser ativo?". No instante em que existir uma linha com
role='client', esse cliente passaria nas 86 rotas de `/admin` — entre elas
`GET /admin/agents/{loc}/agent`, que devolve `api_key`, `anthropic_api_key`,
`elevenlabs_api_key` e o prompt em texto puro de QUALQUER tenant.

O teste de inventário no fim é o que mantém isso verdadeiro depois: rota nova sob
`/admin` que não recuse cliente derruba a suíte, sem depender de ninguém lembrar.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def ambiente(tmp_path, monkeypatch):
    """Um operador e um cliente, cada um com seu cookie válido."""
    from data.database import Base
    import data.models  # noqa: F401
    from data.models import AdminUser, Organization, Tenant
    import services.admin_auth as auth
    from utils.config import settings

    engine = create_engine(
        f"sqlite:///{tmp_path/'barreira.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(auth, "SessionLocal", Session, raising=True)
    monkeypatch.setattr(settings, "debug", True, raising=False)
    from utils.limiter import limiter
    monkeypatch.setattr(limiter, "enabled", False, raising=False)

    db = Session()
    org = Organization(name="Joorney")
    db.add(org)
    db.flush()
    db.add(Tenant(location_id="wp_do_cliente", company_name="Joorney", organization_id=org.id))
    db.add(Tenant(location_id="wp_de_outro", company_name="Outra Empresa"))
    db.add(AdminUser(username="luiz", password_hash=auth.hash_password("senha-operador"),
                     is_active=True, role=auth.OPERADOR))
    db.add(AdminUser(username="joorney", password_hash=auth.hash_password("senha-cliente"),
                     is_active=True, role=auth.CLIENTE, organization_id=org.id))
    db.commit()

    def cookie(username):
        u = db.query(AdminUser).filter_by(username=username).first()
        return auth.make_session_value(u.username, u.password_hash)

    import main

    return {
        "client": TestClient(main.app, raise_server_exceptions=False),
        "cookie_operador": cookie("luiz"),
        "cookie_cliente": cookie("joorney"),
    }


# --------------------------------------------------------------------------- #
# Principal
# --------------------------------------------------------------------------- #
def test_o_principal_do_cliente_alcanca_so_os_tenants_da_organizacao(ambiente):
    from services.admin_auth import resolve_principal

    p = resolve_principal(ambiente["cookie_cliente"])
    assert p is not None
    assert p.is_operator is False
    assert p.locations == ("wp_do_cliente",)
    assert p.alcanca("wp_do_cliente") is True
    assert p.alcanca("wp_de_outro") is False, "cliente alcança tenant de outra empresa"


def test_o_principal_do_operador_alcanca_qualquer_tenant(ambiente):
    from services.admin_auth import resolve_principal

    p = resolve_principal(ambiente["cookie_operador"])
    assert p.is_operator is True
    assert p.alcanca("wp_de_outro") is True


def test_tirar_o_tenant_da_organizacao_revoga_na_hora(ambiente):
    """
    O escopo é lido do BANCO a cada request. Se viajasse no cookie, revogar só
    valeria depois que o cliente trocasse a senha.
    """
    import services.admin_auth as auth
    from data.models import Tenant

    db = auth.SessionLocal()
    db.query(Tenant).filter_by(location_id="wp_do_cliente").first().organization_id = None
    db.commit()
    db.close()

    p = auth.resolve_principal(ambiente["cookie_cliente"])
    assert p.locations == ()
    assert p.alcanca("wp_do_cliente") is False


# --------------------------------------------------------------------------- #
# A barreira em si
# --------------------------------------------------------------------------- #
ROTAS_QUE_VAZAVAM = [
    ("GET", "/admin/agents/wp_de_outro/agent"),          # api_key + prompt em texto puro
    ("GET", "/admin/agents/wp_de_outro/list"),
    ("GET", "/admin/agents/wp_de_outro/conversations"),
    ("GET", "/admin/agents/onboarding/submissions"),     # sem location = todos os tenants
    ("GET", "/admin/agents/prompt-history/1"),           # id sequencial, sem tenant
    ("GET", "/admin/dashboard"),
]


@pytest.mark.parametrize("metodo,url", ROTAS_QUE_VAZAVAM)
def test_cliente_nao_entra_em_rota_de_admin(ambiente, metodo, url):
    r = ambiente["client"].request(
        metodo, url, cookies={"admin_session": ambiente["cookie_cliente"]},
        follow_redirects=False,
    )
    assert r.status_code in (401, 403) or r.headers.get("location", "").startswith("/admin/login"), (
        f"{metodo} {url} respondeu {r.status_code} para um CLIENTE"
    )
    corpo = r.text.lower()
    for vazamento in ("api_key", "anthropic_api_key", "elevenlabs_api_key", "pit_token"):
        assert vazamento not in corpo, f"{url} devolveu {vazamento} para um cliente"


@pytest.mark.parametrize("metodo,url", ROTAS_QUE_VAZAVAM)
def test_operador_continua_entrando(ambiente, metodo, url):
    """A barreira não pode ter fechado a porta da equipe junto."""
    r = ambiente["client"].request(
        metodo, url, cookies={"admin_session": ambiente["cookie_operador"]},
        follow_redirects=False,
    )
    assert r.status_code not in (401, 403), f"{metodo} {url} recusou o OPERADOR ({r.status_code})"


def test_inventario_nenhuma_rota_de_admin_aceita_cliente(ambiente):
    """
    Guarda de construção: varre TODAS as rotas registradas sob /admin e falha se
    alguma responder a um cliente. É o que mantém a barreira depois que ninguém
    estiver mais olhando este arquivo.
    """
    import main

    c, cookie = ambiente["client"], ambiente["cookie_cliente"]
    vazando = []

    for rota in main.app.routes:
        caminho = getattr(rota, "path", "")
        if not caminho.startswith("/admin") or caminho == "/admin/login":
            continue
        for metodo in sorted(getattr(rota, "methods", set()) - {"HEAD", "OPTIONS"}):
            # Preenche path params com valores do tenant de OUTRA empresa.
            url = caminho.replace("{location_id}", "wp_de_outro").replace("{history_id}", "1")
            url = url.replace("{submission_id}", "1").replace("{action}", "status")
            url = url.replace("{agent_id}", "1").replace("{tenant_id}", "wp_de_outro")
            if "{" in url:
                continue  # param desconhecido: não dá para exercitar às cegas
            r = c.request(metodo, url, cookies={"admin_session": cookie}, follow_redirects=False)

            # 401/403 = recusa explícita. 422 = corpo inválido (a request não
            # aconteceu). 503 = gate de env do /inspect. Redirect para o login
            # também é recusa. O que sobra e responde 2xx com conteúdo é leak.
            recusado = (
                r.status_code in (401, 403, 422, 503)
                or r.headers.get("location", "").startswith("/admin/login")
                or "unauthorized" in r.text.lower()
                or "não autenticado" in r.text.lower()
            )
            if not recusado:
                vazando.append(f"{metodo} {url} -> {r.status_code} :: {r.text[:80]}")

    assert not vazando, "rotas de /admin aceitando CLIENTE:\n  " + "\n  ".join(vazando)
