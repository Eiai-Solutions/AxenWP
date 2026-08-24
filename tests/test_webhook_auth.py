"""
Autenticidade dos webhooks de entrada.

Até 2026-08-20 os três receivers validavam só o FORMATO do `location_id` — e o
`/health` público entregava os location_ids de graça. Quem juntasse as duas coisas
fazia POST forjado e o hub tratava como mensagem real: criava contato no CRM,
rodava o agente (gastando LLM do cliente), mandava WhatsApp PELO NÚMERO DO CLIENTE
para o telefone escolhido pelo atacante, e gravava o texto dele em
`chat_histories` — injeção de prompt persistente no contexto do agente.

O que estes testes protegem, em ordem:

1. **Os três estados.** Ligar exigência de uma vez, com atendimento no ar, é perder
   mensagem: o webhook já registrado no provedor ainda não assina. `observe`
   verifica, conta e ACEITA — é ele que torna o rollout seguro.
2. **Canal sem segredo é `off`**, faça o que fizer com o modo. Não se exige
   assinatura de quem não tem com o que assinar.
3. **`enforce` rejeita de verdade.**
"""

from types import SimpleNamespace

import hashlib
import hmac

import pytest

from services import webhook_auth


@pytest.fixture(autouse=True)
def limpo(monkeypatch):
    """Cada teste declara o próprio ambiente — sem herdar env de produção."""
    from utils.config import settings

    for campo in ("waha_webhook_hmac_key", "zapi_webhook_secret", "telegram_webhook_secret"):
        monkeypatch.setattr(settings, campo, "", raising=False)
    monkeypatch.setattr(settings, "webhook_auth_mode", webhook_auth.OBSERVE, raising=False)
    return settings


def _cfg(settings, monkeypatch, **kw):
    for k, v in kw.items():
        monkeypatch.setattr(settings, k, v, raising=False)


def _hmac_waha(chave: str, corpo: bytes) -> str:
    return hmac.new(chave.encode(), corpo, hashlib.sha512).hexdigest()


# ── Sem segredo: nada muda ──

def test_sem_segredo_o_canal_fica_aberto_em_qualquer_modo(limpo, monkeypatch):
    """Exigir assinatura de quem não tem chave só rejeitaria todo mundo."""
    for modo in (webhook_auth.OFF, webhook_auth.OBSERVE, webhook_auth.ENFORCE):
        _cfg(limpo, monkeypatch, webhook_auth_mode=modo)
        assert webhook_auth.verificar_waha(b"{}", {}) is True
        assert webhook_auth.verificar_telegram({}) is True
        assert webhook_auth.verificar_zapi({}, None) is True


def test_o_painel_mostra_off_para_canal_sem_segredo(limpo, monkeypatch):
    _cfg(limpo, monkeypatch, webhook_auth_mode=webhook_auth.ENFORCE,
         waha_webhook_hmac_key="k")
    estado = webhook_auth.estado_para_o_painel()
    assert estado["modo"] == webhook_auth.ENFORCE
    assert estado["canais"]["whatsapp_waha"]["efetivo"] == webhook_auth.ENFORCE
    assert estado["canais"]["telegram"]["efetivo"] == webhook_auth.OFF, (
        "canal sem segredo apareceu como se estivesse protegido"
    )


# ── Os três estados: o que torna o rollout seguro ──

def test_OBSERVE_aceita_assinatura_errada_e_ENFORCE_rejeita(limpo, monkeypatch):
    """
    O par que decide tudo. Se `observe` rejeitasse, configurar o segredo derrubaria
    o atendimento no mesmo instante — e ninguém configuraria.
    """
    _cfg(limpo, monkeypatch, waha_webhook_hmac_key="chave-secreta")

    _cfg(limpo, monkeypatch, webhook_auth_mode=webhook_auth.OBSERVE)
    assert webhook_auth.verificar_waha(b'{"a":1}', {"x-webhook-hmac": "errado"}) is True

    _cfg(limpo, monkeypatch, webhook_auth_mode=webhook_auth.ENFORCE)
    assert webhook_auth.verificar_waha(b'{"a":1}', {"x-webhook-hmac": "errado"}) is False


def test_modo_invalido_cai_em_OBSERVE_e_nao_em_enforce(limpo, monkeypatch):
    """
    Um typo no env (`enforece`) não pode virar rejeição de tudo. Cair no seguro é
    a diferença entre um deploy morno e um atendimento mudo.
    """
    _cfg(limpo, monkeypatch, waha_webhook_hmac_key="k", webhook_auth_mode="enforece")
    assert webhook_auth.modo() == webhook_auth.OBSERVE
    assert webhook_auth.verificar_waha(b"{}", {}) is True


def test_OFF_nem_verifica_e_nao_SUJA_a_metrica(limpo, monkeypatch):
    """
    `off` é "não verifica", não "verifica e ignora o resultado".

    A diferença não aparece no aceite — nos dois casos a mensagem passa. Aparece
    na métrica: verificar em `off` faria `falhou` subir num modo que não está
    checando nada, e é exatamente esse número que o operador olha para decidir se
    pode virar para `enforce`. Contaminado, ele nunca zera e o rollout trava.
    """
    from utils import metrics

    def _linhas():
        # As LINHAS inteiras, não a contagem do nome: contar o nome não veria o
        # contador subir num rótulo que já existe.
        return [l for l in metrics.prometheus_text().splitlines()
                if "millochat_webhook_assinatura_total" in l]

    _cfg(limpo, monkeypatch, waha_webhook_hmac_key="k", telegram_webhook_secret="s",
         zapi_webhook_secret="s", webhook_auth_mode=webhook_auth.OFF)
    antes = _linhas()

    assert webhook_auth.verificar_waha(b"{}", {"x-webhook-hmac": "lixo"}) is True
    assert webhook_auth.verificar_telegram({"x-telegram-bot-api-secret-token": "lixo"}) is True
    assert webhook_auth.verificar_zapi({}, "lixo") is True

    assert _linhas() == antes, "modo `off` mexeu na métrica de assinatura"


# ── WAHA ──

def test_waha_aceita_HMAC_correto_do_corpo_CRU(limpo, monkeypatch):
    _cfg(limpo, monkeypatch, waha_webhook_hmac_key="k", webhook_auth_mode=webhook_auth.ENFORCE)
    corpo = b'{"event":"message","payload":{"x":1}}'
    assert webhook_auth.verificar_waha(corpo, {"x-webhook-hmac": _hmac_waha("k", corpo)}) is True


def test_waha_assina_o_CORPO_e_nao_um_JSON_reserializado(limpo, monkeypatch):
    """
    Reserializar muda espaços e ordem de chaves e o HMAC deixa de bater. A falha
    seria intermitente — dependeria do que o provedor mandou naquele payload.
    """
    _cfg(limpo, monkeypatch, waha_webhook_hmac_key="k", webhook_auth_mode=webhook_auth.ENFORCE)
    original = b'{"b": 2,  "a": 1}'
    reserializado = b'{"a": 1, "b": 2}'
    assinatura = _hmac_waha("k", original)
    assert webhook_auth.verificar_waha(original, {"x-webhook-hmac": assinatura}) is True
    assert webhook_auth.verificar_waha(reserializado, {"x-webhook-hmac": assinatura}) is False


def test_waha_sem_header_e_rejeitado_em_enforce(limpo, monkeypatch):
    _cfg(limpo, monkeypatch, waha_webhook_hmac_key="k", webhook_auth_mode=webhook_auth.ENFORCE)
    assert webhook_auth.verificar_waha(b"{}", {}) is False


def test_header_com_lixo_nao_ASCII_rejeita_em_vez_de_explodir(limpo, monkeypatch):
    """
    `hmac.compare_digest` com `str` exige ASCII puro e levanta `TypeError` fora
    dele. O header chega do Starlette decodificado em latin-1, então byte alto no
    header é trivial de mandar. Sem comparar em bytes, um scanner qualquer vira
    500 no log — e polui exatamente o painel que decide a virada para `enforce`.
    """
    _cfg(limpo, monkeypatch, waha_webhook_hmac_key="k", telegram_webhook_secret="s",
         zapi_webhook_secret="s", webhook_auth_mode=webhook_auth.ENFORCE)

    assert webhook_auth.verificar_waha(b"{}", {"x-webhook-hmac": "café☕"}) is False
    assert webhook_auth.verificar_telegram({"x-telegram-bot-api-secret-token": "café"}) is False
    assert webhook_auth.verificar_zapi({"x-chat-secret": "café"}, None) is False
    assert webhook_auth.verificar_zapi({}, "café") is False


def test_waha_aceita_hex_MAIUSCULO(limpo, monkeypatch):
    """Hex é case-insensitive; recusar maiúscula recusaria provedor legítimo."""
    _cfg(limpo, monkeypatch, waha_webhook_hmac_key="k", webhook_auth_mode=webhook_auth.ENFORCE)
    corpo = b'{"a":1}'
    assinatura = _hmac_waha("k", corpo).upper()
    assert webhook_auth.verificar_waha(corpo, {"x-webhook-hmac": assinatura}) is True


def test_o_segredo_do_telegram_e_case_SENSITIVE(limpo, monkeypatch):
    """
    O oposto do hex: normalizar caixa de segredo joga fora entropia. `S3gr3d0` e
    `s3gr3d0` não podem ser a mesma coisa.
    """
    _cfg(limpo, monkeypatch, telegram_webhook_secret="S3gr3d0",
         webhook_auth_mode=webhook_auth.ENFORCE)
    assert webhook_auth.verificar_telegram(
        {"x-telegram-bot-api-secret-token": "s3gr3d0"}) is False


# ── Telegram ──

def test_telegram_usa_o_header_NATIVO(limpo, monkeypatch):
    _cfg(limpo, monkeypatch, telegram_webhook_secret="s3gr3d0",
         webhook_auth_mode=webhook_auth.ENFORCE)
    assert webhook_auth.verificar_telegram(
        {"x-telegram-bot-api-secret-token": "s3gr3d0"}) is True
    assert webhook_auth.verificar_telegram(
        {"x-telegram-bot-api-secret-token": "outro"}) is False
    assert webhook_auth.verificar_telegram({}) is False


def test_o_setWebhook_manda_o_secret_token_quando_existe(monkeypatch):
    """Sem mandar no registro, o Telegram nunca envia o header — e nada verifica."""
    import asyncio

    from utils.config import settings
    from services.telegram_service import telegram_service

    enviados = {}

    class _Resp:
        status_code = 200
        text = "{}"

        @staticmethod
        def json():
            return {"ok": True}

    class _Cli:
        async def post(self, url, json=None):
            enviados.update(json or {})
            return _Resp()

    monkeypatch.setattr(telegram_service, "_client", _Cli(), raising=False)
    monkeypatch.setattr(type(telegram_service), "client",
                        property(lambda self: _Cli()), raising=False)

    monkeypatch.setattr(settings, "telegram_webhook_secret", "", raising=False)
    asyncio.run(telegram_service.set_webhook("tok", "https://x/webhook"))
    assert "secret_token" not in enviados, "mandou secret_token vazio (o Telegram recusa)"

    enviados.clear()
    monkeypatch.setattr(settings, "telegram_webhook_secret", "s3gr3d0", raising=False)
    asyncio.run(telegram_service.set_webhook("tok", "https://x/webhook"))
    assert enviados.get("secret_token") == "s3gr3d0"


# ── Z-API ──

def test_zapi_aceita_por_header_ou_por_caminho(limpo, monkeypatch):
    _cfg(limpo, monkeypatch, zapi_webhook_secret="s3g", webhook_auth_mode=webhook_auth.ENFORCE)
    assert webhook_auth.verificar_zapi({"x-chat-secret": "s3g"}, None) is True
    assert webhook_auth.verificar_zapi({}, "s3g") is True
    assert webhook_auth.verificar_zapi({}, "errado") is False
    assert webhook_auth.verificar_zapi({}, None) is False


def test_o_header_tem_precedencia_sobre_o_caminho(limpo, monkeypatch):
    """O caminho é a segunda opção; quando os dois vêm, vale o que não vai no log."""
    _cfg(limpo, monkeypatch, zapi_webhook_secret="s3g", webhook_auth_mode=webhook_auth.ENFORCE)
    assert webhook_auth.verificar_zapi({"x-chat-secret": "s3g"}, "lixo") is True


def test_a_rota_com_segredo_no_caminho_existe(limpo):
    """
    Duas rotas em vez de parâmetro opcional: o FastAPI recusa default em parâmetro
    de caminho, e torná-lo opcional o transformaria em QUERYSTRING — que vaza no
    log de acesso E no `Referer`.
    """
    import webhooks.zapi_receiver as zr

    caminhos = {r.path for r in zr.router.routes}
    assert "/webhook/zapi/inbound/{location_id}" in caminhos
    assert "/webhook/zapi/inbound/{location_id}/{segredo}" in caminhos


# ── A métrica que guia o rollout ──

def test_a_metrica_separa_ok_de_falhou(limpo, monkeypatch):
    """
    É olhando `falhou` cair a zero que o operador sabe que pode virar para
    `enforce`. Sem isso, virar a chave é apostar.
    """
    from utils import metrics

    _cfg(limpo, monkeypatch, waha_webhook_hmac_key="k", webhook_auth_mode=webhook_auth.OBSERVE)
    corpo = b"{}"
    webhook_auth.verificar_waha(corpo, {"x-webhook-hmac": _hmac_waha("k", corpo)})
    webhook_auth.verificar_waha(corpo, {"x-webhook-hmac": "errado"})

    texto = metrics.render() if hasattr(metrics, "render") else str(metrics.snapshot())
    assert "millochat_webhook_assinatura_total" in texto
    assert "ok" in texto and "falhou" in texto
