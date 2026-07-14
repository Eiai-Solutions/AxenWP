"""
Caracterização de ZAPIChannel.parse_inbound.

Trava a normalização do payload Z-API (antes embutida em
process_inbound_message) para que a extração para o adapter não regrida e para
que o futuro WAHAChannel tenha um contrato de referência.
"""

from channels.whatsapp.zapi import ZAPIChannel


def _parse(payload):
    return ZAPIChannel().parse_inbound("loc1", payload)


class TestHeaderFields:
    def test_basic_headers(self):
        pm = _parse({"phone": "5511999", "type": "ReceivedCallback",
                     "isGroup": False, "fromMe": False, "messageId": "M1"})
        assert pm.channel == "whatsapp" and pm.provider == "zapi"
        assert pm.location_id == "loc1"
        assert pm.sender_id == "5511999"
        assert pm.message_type == "ReceivedCallback"
        assert pm.is_group is False and pm.from_me is False
        assert pm.provider_message_id == "M1"
        assert pm.event_kind == "message"

    def test_sender_id_preserves_lid(self):
        pm = _parse({"phone": "12345@lid", "text": {"message": "oi"}})
        assert pm.sender_id == "12345@lid"  # normalização é adiante, não aqui

    def test_msg_id_from_ids_fallback(self):
        pm = _parse({"phone": "x", "ids": ["IDX"]})
        assert pm.provider_message_id == "IDX"

    def test_msg_id_prefers_messageId_over_ids(self):
        pm = _parse({"phone": "x", "messageId": "M1", "ids": ["IDX"]})
        assert pm.provider_message_id == "M1"

    def test_sender_name_senderName(self):
        pm = _parse({"phone": "x", "senderName": "João"})
        assert pm.sender_name == "João"

    def test_sender_name_participant_fallback(self):
        pm = _parse({"phone": "x", "participantName": "Maria"})
        assert pm.sender_name == "Maria"


class TestContentParsing:
    def test_text_message(self):
        pm = _parse({"phone": "x", "text": {"message": "olá mundo"}})
        assert pm.text == "olá mundo"
        assert pm.is_audio is False and pm.attachments == []

    def test_image_with_caption_and_url(self):
        pm = _parse({"phone": "x", "image": {"caption": "legenda", "imageUrl": "http://img"}})
        assert pm.text == "legenda"
        assert pm.attachments == ["http://img"]

    def test_image_default_caption(self):
        pm = _parse({"phone": "x", "image": {"imageUrl": "http://img"}})
        assert pm.text == "📸 Imagem recebida"
        assert pm.attachments == ["http://img"]

    def test_audio_audioUrl(self):
        pm = _parse({"phone": "x", "audio": {"audioUrl": "http://a"}})
        assert pm.is_audio is True
        assert pm.audio_url == "http://a"
        assert pm.attachments == ["http://a"]
        assert pm.text == "🎙️ Áudio recebido"

    def test_audio_url_fallback(self):
        pm = _parse({"phone": "x", "audio": {"url": "http://b"}})
        assert pm.audio_url == "http://b"

    def test_audio_mediaUrl_fallback(self):
        pm = _parse({"phone": "x", "audio": {"mediaUrl": "http://c"}})
        assert pm.audio_url == "http://c"

    def test_audio_no_url(self):
        pm = _parse({"phone": "x", "audio": {"seconds": 3}})
        assert pm.is_audio is True
        assert pm.audio_url is None
        assert pm.attachments == []

    def test_voice_treated_as_audio(self):
        pm = _parse({"phone": "x", "voice": {"url": "http://v"}})
        assert pm.is_audio is True
        assert pm.audio_url == "http://v"

    def test_document(self):
        pm = _parse({"phone": "x", "document": {"fileName": "nota.pdf", "documentUrl": "http://d"}})
        assert pm.text == "nota.pdf"
        assert pm.attachments == ["http://d"]

    def test_unknown_payload_default(self):
        pm = _parse({"phone": "x"})
        assert pm.text == "Mensagem recebida do WhatsApp"
        assert pm.is_audio is False and pm.attachments == []

    def test_raw_preserved(self):
        payload = {"phone": "x", "text": {"message": "oi"}}
        pm = _parse(payload)
        assert pm.raw is payload
