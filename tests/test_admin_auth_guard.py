"""
Nenhuma rota administrativa responde sem autenticação.

Regressão real: `admin/ai_agent.py` tinha 25 rotas e NENHUMA checava o cookie —
`GET /admin/agents/{location_id}/agent` devolvia `api_key`, `elevenlabs_api_key`
e o prompt inteiro em texto puro para qualquer um com a URL. A causa foi
`verify_admin` devolver `bool`: quem esquecia de checar o retorno deixava a rota
aberta, silenciosamente.

A defesa é `require_admin` no APIRouter — recusa em vez de informar, e vale por
construção. Este teste garante que ninguém volte a publicar rota admin sem barreira.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _admin_password_configurado(monkeypatch):
    """
    Sem ADMIN_PASSWORD o guard levanta RuntimeError (500) em vez de 401 — nega
    acesso do mesmo jeito, mas aqui queremos provar a recusa DELIBERADA (401).

    Agimos no objeto `settings` e não em os.environ: o Settings do pydantic é
    instanciado no import de utils/config.py, então quando a suíte inteira roda
    (outro teste já importou o módulo antes) mexer na env var não teria efeito.
    """
    from utils.config import settings

    monkeypatch.setattr(settings, "admin_password", "senha-de-teste", raising=False)


@pytest.fixture(scope="module")
def client():
    import main
    return TestClient(main.app, raise_server_exceptions=False)


# Amostra representativa: leitura de config sensível, escrita, e ações de motor.
ROTAS_SENSIVEIS = [
    ("GET", "/admin/agents/qualquerlocation123/agent"),
    ("GET", "/admin/agents/qualquerlocation123/list"),
    ("GET", "/admin/agents/qualquerlocation123/conversations"),
    ("GET", "/admin/agents/onboarding/submissions"),
    ("GET", "/admin/agents/elevenlabs/voices"),
    ("POST", "/admin/agents/analyze-prompt"),
    ("POST", "/admin/agents/master-chat"),
]


@pytest.mark.parametrize("metodo,rota", ROTAS_SENSIVEIS)
def test_rota_admin_recusa_sem_cookie(client, metodo, rota):
    r = client.request(metodo, rota)
    assert r.status_code in (401, 403), (
        f"{metodo} {rota} respondeu {r.status_code} sem autenticação — "
        f"rota admin nunca pode responder a anônimo."
    )


def test_get_do_agente_nao_vaza_chave_para_anonimo(client):
    """O vazamento concreto que motivou o guard: chaves de LLM em texto puro."""
    r = client.get("/admin/agents/qualquerlocation123/agent")
    assert r.status_code in (401, 403)
    corpo = r.text.lower()
    for termo in ("api_key", "sk-or-", "elevenlabs", "prompt"):
        assert termo not in corpo, f"resposta a anônimo contém '{termo}'"


def test_router_de_agentes_tem_barreira_no_proprio_router():
    """
    A proteção precisa estar no APIRouter, não rota a rota — senão a próxima rota
    criada nasce aberta de novo (foi exatamente assim que o buraco surgiu).
    """
    from admin.ai_agent import router

    nomes = {getattr(d.dependency, "__name__", "") for d in (router.dependencies or [])}
    assert "require_admin" in nomes, "o router de /admin/agents perdeu a barreira global"


def test_require_admin_levanta_e_nao_devolve_bool():
    """`verify_admin` informa; `require_admin` recusa. A diferença é o bug."""
    from fastapi import HTTPException
    from admin.dashboard import require_admin

    with pytest.raises(HTTPException) as exc:
        require_admin(admin_session=None)
    assert exc.value.status_code == 401
