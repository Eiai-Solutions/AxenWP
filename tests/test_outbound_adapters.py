"""
Envio pelo adapter — paridade com o que o ghl_provider mandava antes.

Estes testes existem por um motivo específico: Joorney, Kozan e MapInvest rodam
em Z-API em produção. Ao trocar as chamadas diretas a `zapi_service` por
`ZAPIChannel`, qualquer argumento perdido no caminho (client_token,
delay_typing, record_audio) viraria regressão silenciosa. Aqui asseguramos que
o adapter é fachada, não reimplementação.
"""

from types import SimpleNamespace

import pytest

from channels.registry import resolve_send_adapter
from channels.whatsapp.waha import WAHAChannel
from channels.whatsapp.zapi import ZAPIChannel


def tenant_zapi(**kw):
    base = dict(
        location_id="loc1",
        whatsapp_provider="zapi",
        waha_session=None,
        zapi_instance_id="3EB1",
        zapi_token="tok",
        zapi_client_token="ctok",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def tenant_waha(**kw):
    base = dict(
        location_id="loc1",
        whatsapp_provider="waha",
        waha_session="loc1",
        waha_base_url="https://waha.exemplo",
        waha_api_key="k",
        zapi_instance_id=None,
        zapi_token=None,
        zapi_client_token=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


class SpyZapi:
    """Captura os kwargs exatos que o adapter passa ao serviço."""

    _PADRAO = object()

    def __init__(self, retorno=_PADRAO):
        self.chamadas = []
        self.retorno = {"zapiMessageId": "ZID"} if retorno is SpyZapi._PADRAO else retorno

    async def _rec(self, nome, **kw):
        self.chamadas.append((nome, kw))
        return self.retorno

    async def send_text(self, **kw):
        return await self._rec("send_text", **kw)

    async def send_image(self, **kw):
        return await self._rec("send_image", **kw)

    async def send_audio(self, **kw):
        return await self._rec("send_audio", **kw)

    async def send_document(self, **kw):
        return await self._rec("send_document", **kw)


@pytest.fixture
def spy(monkeypatch):
    s = SpyZapi()
    monkeypatch.setattr("channels.whatsapp.zapi.zapi_service", s)
    return s


# ── Z-API: paridade de argumentos ──

@pytest.mark.asyncio
async def test_zapi_send_text_preserva_credenciais_e_delay(spy):
    res = await ZAPIChannel().send_text(tenant_zapi(), "5547999", "oi", typing_delay=5)
    nome, kw = spy.chamadas[0]
    assert nome == "send_text"
    assert kw == {
        "instance_id": "3EB1", "token": "tok", "phone": "5547999",
        "message": "oi", "client_token": "ctok", "delay_typing": 5,
    }
    assert res.ok and res.provider_message_id == "ZID"


@pytest.mark.asyncio
async def test_zapi_send_image_leva_caption(spy):
    await ZAPIChannel().send_image(tenant_zapi(), "5547999", "http://x/y.png", caption="olha")
    _, kw = spy.chamadas[0]
    assert kw["image_url"] == "http://x/y.png" and kw["caption"] == "olha"
    assert kw["client_token"] == "ctok"


@pytest.mark.asyncio
async def test_zapi_send_audio_mantem_record_audio(spy):
    # record_audio=True é o que faz a mensagem chegar como PTT e não como arquivo.
    await ZAPIChannel().send_audio(tenant_zapi(), "5547999", "http://x/a.ogg")
    _, kw = spy.chamadas[0]
    assert kw["record_audio"] is True


@pytest.mark.asyncio
async def test_zapi_send_document_leva_filename(spy):
    await ZAPIChannel().send_document(tenant_zapi(), "5547999", "http://x/f.pdf", filename="Proposta.pdf")
    _, kw = spy.chamadas[0]
    assert kw["document_url"] == "http://x/f.pdf" and kw["filename"] == "Proposta.pdf"


@pytest.mark.asyncio
async def test_zapi_client_token_ausente_vira_string_vazia(spy):
    await ZAPIChannel().send_text(tenant_zapi(zapi_client_token=None), "5547999", "oi")
    _, kw = spy.chamadas[0]
    assert kw["client_token"] == ""


@pytest.mark.asyncio
async def test_zapi_falha_do_servico_vira_result_nao_ok(monkeypatch):
    s = SpyZapi(retorno=None)
    monkeypatch.setattr("channels.whatsapp.zapi.zapi_service", s)
    res = await ZAPIChannel().send_text(tenant_zapi(), "5547999", "oi")
    assert res.ok is False and res.provider_message_id is None


# ── WAHA: normalização do destinatário ──

@pytest.mark.parametrize("entrada,esperado", [
    ("+55 47 99720-4869", "5547997204869@c.us"),   # como o CRM entrega
    ("5547997204869", "5547997204869@c.us"),
    ("5547997204869@c.us", "5547997204869@c.us"),  # já é jid, não mexe
    ("(47) 99720-4869", "4799720486 9".replace(" ", "") + "@c.us"),
])
def test_waha_chat_id_normaliza(entrada, esperado):
    assert WAHAChannel()._chat_id(entrada) == esperado


def test_waha_chat_id_preserva_lid():
    # Identidade de lead de anúncio não pode virar "76412417495205@c.us".
    assert WAHAChannel()._chat_id("76412417495205@lid") == "76412417495205@lid"


# ── Resolução do adapter de envio ──

def test_resolve_send_adapter_waha():
    assert isinstance(resolve_send_adapter(tenant_waha()), WAHAChannel)


def test_resolve_send_adapter_zapi():
    assert isinstance(resolve_send_adapter(tenant_zapi()), ZAPIChannel)


def test_resolve_send_adapter_sem_provedor_devolve_none():
    # Flag diz waha, mas não há sessão: não existe para onde enviar.
    assert resolve_send_adapter(tenant_waha(waha_session=None)) is None
    assert resolve_send_adapter(tenant_zapi(zapi_instance_id=None, zapi_token=None)) is None


def test_credentials_ok():
    assert ZAPIChannel().credentials_ok(tenant_zapi()) is True
    assert ZAPIChannel().credentials_ok(tenant_zapi(zapi_token=None)) is False
    assert WAHAChannel().credentials_ok(tenant_waha()) is True


# ── WAHA: os três defeitos que a revisão adversarial pegou ──

class SpyWaha:
    def __init__(self, retorno=None):
        self.chamadas = []
        self.retorno = retorno

    async def send_voice(self, base, key, session, chat_id, audio_b64=None, *, audio_url=None):
        self.chamadas.append(("send_voice", {"b64": audio_b64, "url": audio_url}))
        return self.retorno

    async def send_text(self, base, key, session, chat_id, text):
        self.chamadas.append(("send_text", {"text": text}))
        return self.retorno


@pytest.fixture
def spy_waha(monkeypatch):
    s = SpyWaha(retorno={"id": "WID"})
    monkeypatch.setattr("channels.whatsapp.waha.waha_service", s)
    monkeypatch.setattr("channels.whatsapp.waha.get_global_waha_config", lambda: ("https://w", "k"))
    return s


@pytest.mark.asyncio
async def test_audio_por_url_do_crm_vai_como_url_e_nao_como_base64(spy_waha):
    # Anexo do CRM é URL http. Mandar isso em file.data faria o áudio nunca sair.
    await WAHAChannel().send_audio(tenant_waha(), "5547999", "https://storage.googleapis.com/a/b.mp3")
    _, kw = spy_waha.chamadas[0]
    assert kw["url"] == "https://storage.googleapis.com/a/b.mp3"
    assert kw["b64"] is None


@pytest.mark.asyncio
async def test_audio_com_virgula_na_url_nao_e_cortado(spy_waha):
    url = "https://storage.googleapis.com/a/b.ogg?x=1,2"
    await WAHAChannel().send_audio(tenant_waha(), "5547999", url)
    _, kw = spy_waha.chamadas[0]
    assert kw["url"] == url          # inteira
    assert kw["b64"] is None         # e não como "2"


@pytest.mark.asyncio
async def test_audio_do_tts_continua_indo_como_base64(spy_waha):
    await WAHAChannel().send_audio(tenant_waha(), "5547999", "data:audio/ogg;base64,QUJD")
    _, kw = spy_waha.chamadas[0]
    assert kw["b64"] == "QUJD" and kw["url"] is None


@pytest.mark.parametrize("resposta,esperado", [
    ({"id": "abc"}, "abc"),
    ({"key": {"id": "abc"}}, "abc"),
    ({"_data": {"id": "abc"}}, "abc"),
    ({"id": {"_serialized": "true_55@c.us_ABC"}}, "true_55@c.us_ABC"),  # forma de objeto
    ({"id": {"algo": 1}}, None),   # objeto sem id utilizável → None, nunca o dict
    ({}, None),
    (None, None),
    ({"id": ""}, None),
])
def test_extract_message_id_sempre_string_ou_none(resposta, esperado):
    assert WAHAChannel()._extract_message_id(resposta) == esperado


@pytest.mark.asyncio
async def test_resposta_vazia_e_aceita_mas_sem_id(monkeypatch):
    # 200 com corpo vazio = provedor aceitou. Marcar 'failed' faria o operador
    # reenviar e o cliente receber duas vezes.
    s = SpyWaha(retorno={})
    monkeypatch.setattr("channels.whatsapp.waha.waha_service", s)
    monkeypatch.setattr("channels.whatsapp.waha.get_global_waha_config", lambda: ("https://w", "k"))
    res = await WAHAChannel().send_text(tenant_waha(), "5547999", "oi")
    assert res.ok is True and res.provider_message_id is None


@pytest.mark.asyncio
async def test_falha_de_rede_nao_e_sucesso(monkeypatch):
    s = SpyWaha(retorno=None)
    monkeypatch.setattr("channels.whatsapp.waha.waha_service", s)
    monkeypatch.setattr("channels.whatsapp.waha.get_global_waha_config", lambda: ("https://w", "k"))
    res = await WAHAChannel().send_text(tenant_waha(), "5547999", "oi")
    assert res.ok is False
