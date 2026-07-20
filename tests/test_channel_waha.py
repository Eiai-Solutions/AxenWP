"""
Caracterização de WAHAChannel.parse_inbound e da resolução de provedor.

Trava o contrato contra o schema de webhook documentado do WAHA (evento
`message`), para que a normalização bata com a do ZAPIChannel e o pipeline
compartilhado trate os dois igual. Envio/PTT são validados ao vivo (precisam da
instância WAHA); aqui cobrimos parse + seleção de adapter.
"""

from types import SimpleNamespace

from channels.registry import resolve_whatsapp_adapter
from channels.whatsapp.waha import WAHAChannel
from channels.whatsapp.zapi import ZAPIChannel


def _msg(payload_inner, event="message"):
    return {"event": event, "session": "s1", "payload": payload_inner}


def _parse(payload_inner, event="message"):
    return WAHAChannel().parse_inbound("loc1", _msg(payload_inner, event))


class TestWahaParse:
    def test_text_message(self):
        pm = _parse({"id": "true_5511@c.us_X", "from": "5511999@c.us",
                     "fromMe": False, "body": "oi", "hasMedia": False})
        assert pm is not None
        assert pm.channel == "whatsapp" and pm.provider == "waha"
        assert pm.sender_id == "5511999"          # @c.us removido
        assert pm.provider_message_id == "true_5511@c.us_X"
        assert pm.text == "oi"
        assert pm.is_audio is False and pm.from_me is False
        assert pm.is_group is False

    def test_preserves_lid(self):
        pm = _parse({"from": "12345@lid", "body": "anuncio"})
        assert pm.sender_id == "12345@lid"        # @lid preservado (lead de anúncio)

    def test_group_detected(self):
        pm = _parse({"from": "12345@g.us", "body": "x"})
        assert pm.is_group is True

    def test_from_me_flag(self):
        pm = _parse({"from": "5511@c.us", "fromMe": True, "body": "eco"})
        assert pm.from_me is True                 # dedup obrigatório (WAHA reentrega)

    def test_audio_message(self):
        pm = _parse({"from": "5511@c.us", "hasMedia": True,
                     "media": {"mimetype": "audio/ogg; codecs=opus", "url": "http://a"}})
        assert pm.is_audio is True
        assert pm.audio_url == "http://a"
        assert pm.media_url == "http://a"
        # A mídia do WAHA exige X-Api-Key: mandá-la como anexo fazia o GHL
        # recusar o inbound INTEIRO com 422 "each value in attachments must be
        # an URL address", perdendo a mensagem e não só o arquivo.
        assert pm.attachments == []

    def test_audio_sem_legenda_ganha_rotulo(self):
        # Texto vazio fazia o turno morrer no guard do pipeline, sem STT e sem resposta.
        pm = _parse({"from": "5511@c.us", "hasMedia": True,
                     "media": {"mimetype": "audio/ogg; codecs=opus", "url": "http://a"}})
        assert pm.text == "🎤 Áudio recebido"

    def test_audio_media_not_downloaded(self):
        # WAHA pode mandar hasMedia:true com media:null (não baixou) — não deve quebrar
        pm = _parse({"from": "5511@c.us", "hasMedia": True, "media": None})
        assert pm.is_audio is False
        assert pm.audio_url is None
        assert pm.attachments == []

    def test_image_with_caption(self):
        pm = _parse({"from": "5511@c.us", "body": "legenda", "hasMedia": True,
                     "media": {"mimetype": "image/jpeg", "url": "http://img"}})
        assert pm.is_audio is False
        assert pm.text == "legenda"          # legenda do usuário tem prioridade
        assert pm.media_url == "http://img"
        assert pm.attachments == []          # ver test_audio_message

    def test_notify_name(self):
        pm = _parse({"from": "5511@c.us", "notifyName": "João", "body": "oi"})
        assert pm.sender_name == "João"

    def test_non_message_event_ignored(self):
        assert _parse({"id": "x"}, event="message.ack") is None
        assert _parse({"id": "x"}, event="session.status") is None


class TestRegistry:
    def test_default_is_zapi(self):
        assert isinstance(resolve_whatsapp_adapter(SimpleNamespace()), ZAPIChannel)
        assert isinstance(resolve_whatsapp_adapter(SimpleNamespace(whatsapp_provider="zapi")), ZAPIChannel)

    def test_waha_selected(self):
        adapter = resolve_whatsapp_adapter(SimpleNamespace(whatsapp_provider="waha"))
        assert isinstance(adapter, WAHAChannel)

    def test_case_insensitive(self):
        assert isinstance(resolve_whatsapp_adapter(SimpleNamespace(whatsapp_provider="WAHA")), WAHAChannel)


class TestWahaCredentials:
    def test_credentials_ok(self):
        ok = SimpleNamespace(waha_base_url="http://w", waha_session="s1", waha_api_key="k")
        assert WAHAChannel().credentials_ok(ok) is True

    def test_credentials_missing(self):
        assert WAHAChannel().credentials_ok(SimpleNamespace()) is False
