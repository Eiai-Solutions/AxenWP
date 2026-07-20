"""
Mídia no inbound: áudio e arquivos.

Dois bugs confirmados em produção (2026-07-20), ambos silenciosos:

1. A URL da mídia do WAHA é interna e autenticada
   (`http://localhost:3000/api/files/…`, 401 sem X-Api-Key). Mandá-la como
   anexo fazia o GHL recusar o inbound INTEIRO:
   `422 "each value in attachments must be an URL address"` →
   `Falha ao espelhar inbound` — a mensagem se perdia, não só o arquivo.

2. Áudio sem legenda tem texto vazio e morria em `if not texto: return`, antes
   do STT. O lead mandava um áudio e o agente simplesmente não respondia.
"""

from types import SimpleNamespace

import pytest

from channels.whatsapp.waha import WAHAChannel, _rotulo_de_midia


def _parse(inner):
    return WAHAChannel().parse_inbound("loc1", {"event": "message", "payload": inner})


# Payload real capturado da produção (nota de voz, GOWS 2026.6.1).
AUDIO_REAL = {
    "id": "false_198101675561023@lid_3EB0CAEEB3AE38BB88B4B0",
    "from": "198101675561023@lid",
    "fromMe": False,
    "body": "",
    "hasMedia": True,
    "media": {
        "url": "http://localhost:3000/api/files/jVxHh2Elz8MxMurLzwzz/3EB0CAEEB3AE38BB88B4B0.oga",
        "mimetype": "audio/ogg; codecs=opus",
    },
    "_data": {"Info": {"SenderAlt": "554797838884:77@s.whatsapp.net", "PushName": "Luiz Antonio"}},
}


class TestAudioReal:
    def test_reconhece_como_audio(self):
        pm = _parse(AUDIO_REAL)
        assert pm.is_audio is True
        assert pm.audio_url.endswith(".oga")
        assert pm.media_mimetype.startswith("audio/")

    def test_nao_vira_anexo_do_crm(self):
        # A regressão que quebrou o espelho inteiro com 422.
        assert _parse(AUDIO_REAL).attachments == []

    def test_ganha_texto_para_nao_ser_descartado(self):
        assert _parse(AUDIO_REAL).text == "🎤 Áudio recebido"

    def test_identidade_continua_resolvida(self):
        # Mídia não pode atrapalhar a resolução de @lid.
        assert _parse(AUDIO_REAL).sender_id == "554797838884"


class TestRotulos:
    @pytest.mark.parametrize("mimetype,esperado", [
        ("audio/ogg; codecs=opus", "🎤 Áudio recebido"),
        ("image/jpeg", "📸 Imagem recebida"),
        ("image/webp", "🩹 Figurinha recebida"),   # figurinha não é foto
        ("video/mp4", "🎬 Vídeo recebido"),
    ])
    def test_por_mimetype(self, mimetype, esperado):
        assert _rotulo_de_midia(mimetype) == esperado

    def test_documento_usa_o_nome_do_arquivo(self):
        assert _rotulo_de_midia("application/pdf", "contrato.pdf") == "📄 Documento recebido: contrato.pdf"

    def test_documento_sem_nome(self):
        assert _rotulo_de_midia("application/pdf") == "📎 Arquivo recebido"

    def test_legenda_do_usuario_vence_o_rotulo(self):
        pm = _parse({**AUDIO_REAL, "body": "ouve isso aqui"})
        assert pm.text == "ouve isso aqui"


class TestMidiaAusente:
    def test_hasmedia_sem_url_nao_quebra(self):
        pm = _parse({"from": "5511@c.us", "hasMedia": True, "media": None})
        assert pm.is_audio is False
        assert pm.media_url is None
        assert pm.attachments == []

    def test_media_com_erro_nao_quebra(self):
        # O WAHA também devolve media:{"error": ...} além de media:null.
        pm = _parse({"from": "5511@c.us", "hasMedia": True,
                     "media": {"error": "failed to download"}})
        assert pm.media_url is None

    def test_documento_propaga_nome(self):
        pm = _parse({"from": "5511@c.us", "hasMedia": True,
                     "media": {"mimetype": "application/pdf", "url": "http://x/a.pdf",
                               "filename": "contrato.pdf"}})
        assert pm.media_filename == "contrato.pdf"
        assert pm.text == "📄 Documento recebido: contrato.pdf"


class TestMediaFetch:
    """A URL interna do WAHA precisa virar alcançável, e a chave vai em header."""

    def _tenant(self):
        return SimpleNamespace(
            location_id="loc1", waha_session="s1",
            waha_base_url="https://waha.exemplo.com", waha_api_key="SEGREDO",
        )

    def test_reescreve_host_interno(self):
        url, headers = WAHAChannel().media_fetch(
            self._tenant(),
            "http://localhost:3000/api/files/s1/abc.oga",
        )
        assert url == "https://waha.exemplo.com/api/files/s1/abc.oga"
        assert headers == {"X-Api-Key": "SEGREDO"}

    def test_credencial_nunca_entra_na_url(self):
        # Embutir a chave na URL a vazaria em log e no histórico do CRM — e ela
        # é a chave GLOBAL do servidor, compartilhada por todos os tenants.
        url, _ = WAHAChannel().media_fetch(
            self._tenant(), "http://localhost:3000/api/files/s1/abc.oga"
        )
        assert "SEGREDO" not in url
        assert "x-api-key" not in url.lower()

    def test_url_vazia(self):
        assert WAHAChannel().media_fetch(self._tenant(), None) == (None, {})

    def test_url_de_outro_host_passa_intacta(self):
        url, _ = WAHAChannel().media_fetch(self._tenant(), "https://cdn.exemplo.com/a.ogg")
        assert url == "https://cdn.exemplo.com/a.ogg"
