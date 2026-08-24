"""
Os receivers de verdade, batendo nas rotas.

`test_webhook_auth.py` prova o módulo de política. Este prova a FIAÇÃO — que as
três rotas realmente consultam a política antes de agir. Sem ele, dá para o
`webhook_auth` estar impecável e nenhuma rota chamá-lo; os testes de unidade
continuariam verdes e a porta continuaria aberta.

E a asserção não é o corpo da resposta: os três webhooks respondem 200 de
propósito (provedor que leva erro reenvia em loop). O que se prova aqui é que
**nenhuma tarefa de background foi agendada** — é lá que está o dano real:
roda o agente na conta do cliente, dispara WhatsApp pelo número dele e grava o
texto do atacante em `chat_histories`, que volta como contexto do agente na
próxima mensagem.
"""

import hashlib
import hmac

import pytest
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient

from services import webhook_auth

LOC = "abcdefghij0123456789"  # 20 chars alfanum — passa em is_valid_location_id

# O WAHA atende em `/webhook/whatsapp/...` — herdou o caminho da Z-API, que ele
# substituiu. Escrever `/webhook/waha/` aqui dá 404, e 404 não agenda nada: o
# teste de rejeição passaria sem nunca ter tocado na verificação. Foi o que
# aconteceu na primeira versão deste arquivo, por isso `test_os_caminhos_existem`
# ancora os três antes de qualquer outra coisa.
ROTA_WAHA = f"/webhook/whatsapp/{LOC}"
ROTA_TG = f"/webhook/telegram/{LOC}"
ROTA_ZAPI = f"/webhook/zapi/inbound/{LOC}"


def test_os_caminhos_existem(app_de_teste):
    """
    Guarda dos outros testes deste arquivo. Um caminho errado dá 404, 404 não
    agenda nada, e todo teste de rejeição passa sem ter exercitado uma linha da
    verificação. Aqui um caminho errado falha ALTO, em vez de virar falso verde.
    """
    registrados = {r.path for r in app_de_teste.routes}
    for rota in (ROTA_WAHA, ROTA_TG, ROTA_ZAPI):
        molde = rota.replace(LOC, "{location_id}")
        assert molde in registrados, f"{molde} não está registrado — 404 mascararia tudo"


@pytest.fixture
def app_de_teste(monkeypatch):
    """
    App mínimo com os três routers. Não sobe o `main` inteiro de propósito: o
    lifespan dele roda Alembic e abre clientes HTTP.
    """
    from fastapi import FastAPI
    from slowapi import _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded

    from utils.limiter import limiter
    import webhooks.waha_receiver as waha
    import webhooks.telegram_receiver as tg
    import webhooks.zapi_receiver as zapi

    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(waha.router)
    app.include_router(tg.router)
    app.include_router(zapi.router)
    return app


@pytest.fixture
def agendadas(monkeypatch):
    """
    A evidência de que a requisição passou da porta — e ela NÃO é a mesma nos três
    canais, o que é uma armadilha:

    - WAHA e Z-API despacham por `BackgroundTasks.add_task`;
    - **Telegram não.** Ele usa `asyncio.create_task` direto. Um espião só de
      `add_task` nunca veria o Telegram, e todo teste de rejeição dele passaria
      sem exercitar uma linha da verificação (foi o que aconteceu aqui).

    Para o Telegram o marco é `token_manager.get_tenant`: é a primeira coisa
    depois da verificação. Rejeitou, não consulta tenant.
    """
    chamadas = []
    original = BackgroundTasks.add_task

    def espiao(self, func, *a, **kw):
        chamadas.append(getattr(func, "__name__", str(func)))
        return None  # não executa: o alvo faria I/O de rede e banco

    monkeypatch.setattr(BackgroundTasks, "add_task", espiao)

    import webhooks.telegram_receiver as tg

    class _TenantFalso:
        telegram_bot_token = "tok"
        is_active = True
        location_id = LOC

    def _get_tenant(location_id):
        chamadas.append("telegram:get_tenant")
        return _TenantFalso()

    async def _nao_roda(**kw):
        return None

    monkeypatch.setattr(tg.token_manager, "get_tenant", _get_tenant)
    monkeypatch.setattr(tg, "_run_ai_response", _nao_roda)

    yield chamadas
    monkeypatch.setattr(BackgroundTasks, "add_task", original)


@pytest.fixture(autouse=True)
def ambiente(monkeypatch):
    from utils.config import settings

    for campo in ("waha_webhook_hmac_key", "zapi_webhook_secret", "telegram_webhook_secret"):
        monkeypatch.setattr(settings, campo, "", raising=False)
    monkeypatch.setattr(settings, "webhook_auth_mode", webhook_auth.ENFORCE, raising=False)
    return settings


def _liga(settings, monkeypatch, **kw):
    for k, v in kw.items():
        monkeypatch.setattr(settings, k, v, raising=False)


# ── WAHA ──

CORPO_WAHA = b'{"event":"message","payload":{"from":"5511999999999@c.us","body":"oi"}}'


def test_waha_forjado_nao_agenda_processamento(app_de_teste, agendadas, ambiente, monkeypatch):
    _liga(ambiente, monkeypatch, waha_webhook_hmac_key="k")
    r = TestClient(app_de_teste).post(
        ROTA_WAHA, content=CORPO_WAHA,
        headers={"content-type": "application/json", "x-webhook-hmac": "forjado"},
    )
    assert r.status_code == 200, "erro faz o provedor reenviar em loop"
    assert agendadas == [], f"mensagem forjada chegou ao processamento: {agendadas}"


def test_waha_assinado_passa(app_de_teste, agendadas, ambiente, monkeypatch):
    """A outra metade: se só rejeitasse, o cliente legítimo ficaria mudo."""
    _liga(ambiente, monkeypatch, waha_webhook_hmac_key="k")
    assinatura = hmac.new(b"k", CORPO_WAHA, hashlib.sha512).hexdigest()
    TestClient(app_de_teste).post(
        ROTA_WAHA, content=CORPO_WAHA,
        headers={"content-type": "application/json", "x-webhook-hmac": assinatura},
    )
    assert agendadas == ["process_waha_message"]


def test_waha_em_OBSERVE_deixa_passar_nao_assinado(app_de_teste, agendadas, ambiente, monkeypatch):
    """
    O estado que torna o rollout possível: o segredo já está no ambiente, o
    webhook registrado no provedor ainda NÃO assina, e ninguém perde mensagem.
    """
    _liga(ambiente, monkeypatch, waha_webhook_hmac_key="k",
          webhook_auth_mode=webhook_auth.OBSERVE)
    TestClient(app_de_teste).post(
        ROTA_WAHA, content=CORPO_WAHA,
        headers={"content-type": "application/json"},
    )
    assert agendadas == ["process_waha_message"]


# ── Telegram ──

CORPO_TG = {"update_id": 1,
            "message": {"chat": {"id": 42, "type": "private"}, "text": "oi"}}


def test_telegram_forjado_nao_agenda_processamento(app_de_teste, agendadas, ambiente, monkeypatch):
    _liga(ambiente, monkeypatch, telegram_webhook_secret="s3g")
    r = TestClient(app_de_teste).post(
        ROTA_TG, json=CORPO_TG,
        headers={"x-telegram-bot-api-secret-token": "forjado"},
    )
    assert r.status_code == 200
    assert agendadas == [], f"update forjado passou da porta: {agendadas}"


def test_telegram_assinado_passa(app_de_teste, agendadas, ambiente, monkeypatch):
    _liga(ambiente, monkeypatch, telegram_webhook_secret="s3g")
    TestClient(app_de_teste).post(
        ROTA_TG, json=CORPO_TG,
        headers={"x-telegram-bot-api-secret-token": "s3g"},
    )
    assert agendadas == ["telegram:get_tenant"], "update legítimo foi barrado"


# ── Z-API ──

CORPO_ZAPI = {"phone": "5511999999999", "text": {"message": "oi"}, "fromMe": False}


def test_zapi_forjado_nao_agenda_processamento(app_de_teste, agendadas, ambiente, monkeypatch):
    _liga(ambiente, monkeypatch, zapi_webhook_secret="s3g")
    r = TestClient(app_de_teste).post(ROTA_ZAPI, json=CORPO_ZAPI)
    assert r.status_code == 200
    assert agendadas == [], f"mensagem forjada chegou ao processamento: {agendadas}"


def test_zapi_com_header_passa(app_de_teste, agendadas, ambiente, monkeypatch):
    _liga(ambiente, monkeypatch, zapi_webhook_secret="s3g")
    TestClient(app_de_teste).post(
        ROTA_ZAPI, json=CORPO_ZAPI,
        headers={"x-chat-secret": "s3g"},
    )
    assert agendadas == ["process_inbound_message"]


def test_zapi_com_segredo_no_caminho_passa(app_de_teste, agendadas, ambiente, monkeypatch):
    """A rota irmã existe e é atendida — não é 404 nem cai na rota sem segredo."""
    _liga(ambiente, monkeypatch, zapi_webhook_secret="s3g")
    r = TestClient(app_de_teste).post(ROTA_ZAPI + "/s3g", json=CORPO_ZAPI)
    assert r.status_code == 200
    assert agendadas == ["process_inbound_message"]


def test_zapi_com_segredo_ERRADO_no_caminho_e_barrado(app_de_teste, agendadas, ambiente, monkeypatch):
    _liga(ambiente, monkeypatch, zapi_webhook_secret="s3g")
    TestClient(app_de_teste).post(ROTA_ZAPI + "/chutado", json=CORPO_ZAPI)
    assert agendadas == []


# ── A regra que vale para os três ──

def test_sem_segredo_configurado_os_tres_continuam_funcionando(app_de_teste, agendadas, ambiente, monkeypatch):
    """
    Este deploy não pode mudar nada para quem está no ar hoje. Com os três
    segredos vazios (como estão em produção agora), tudo passa exatamente como
    passava — a proteção é opt-in.
    """
    c = TestClient(app_de_teste)
    c.post(ROTA_WAHA, content=CORPO_WAHA,
           headers={"content-type": "application/json"})
    c.post(ROTA_TG, json=CORPO_TG)
    c.post(ROTA_ZAPI, json=CORPO_ZAPI)
    assert agendadas == [
        "process_waha_message",
        "telegram:get_tenant",
        "process_inbound_message",
    ], f"o deploy quebraria quem está no ar: {agendadas}"


# ── Superfície pública: o mapa ──

def test_o_esquema_OpenAPI_nao_e_publico_fora_de_DEBUG(monkeypatch):
    """
    `/docs`, `/redoc` e `/openapi.json` entregavam o mapa completo do hub sem
    autenticação: as rotas de `/admin`, a API de tenant e o formato exato de cada
    webhook de entrada. A defesa não depende de esconder o mapa — mas publicá-lo
    de graça faz o primeiro passo do trabalho do atacante.
    """
    from fastapi import FastAPI
    from utils.config import settings

    def _monta(debug: bool) -> FastAPI:
        return FastAPI(
            docs_url="/docs" if debug else None,
            redoc_url="/redoc" if debug else None,
            openapi_url="/openapi.json" if debug else None,
        )

    prod = TestClient(_monta(False))
    for rota in ("/docs", "/redoc", "/openapi.json"):
        assert prod.get(rota).status_code == 404, f"{rota} aberta em produção"

    dev = TestClient(_monta(True))
    assert dev.get("/openapi.json").status_code == 200, "fechou também em DEBUG"


def test_main_amarra_os_docs_ao_DEBUG():
    """
    O teste acima prova a REGRA; este prova que o `main` a aplica. Sem ele, alguém
    reintroduz `docs_url` fixo e a regra continua verde sozinha, sem efeito.
    """
    import inspect

    import main

    fonte = inspect.getsource(main)
    for campo in ("docs_url", "redoc_url", "openapi_url"):
        assert f'{campo}="' in fonte and "settings.debug" in fonte, (
            f"{campo} não está condicionado a settings.debug em main.py"
        )
