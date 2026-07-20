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


class TestPublicMediaUrl:
    """A URL que o CRM consegue baixar aponta para o NOSSO proxy, não para o WAHA."""

    def _tenant(self):
        return SimpleNamespace(
            location_id="loc1", waha_session="s1",
            waha_base_url="https://waha.exemplo.com", waha_api_key="SEGREDO",
        )

    def test_gera_url_do_proxy_e_normaliza_audio(self, monkeypatch):
        from channels.whatsapp import waha as waha_mod
        monkeypatch.setattr(waha_mod.settings, "public_base_url", "https://app.exemplo.com")
        url = WAHAChannel().public_media_url(
            self._tenant(), "http://localhost:3000/api/files/s1/3EB0ABC.oga"
        )
        # .oga -> .ogg para o GHL renderizar como player, não como arquivo.
        assert url == "https://app.exemplo.com/media/whatsapp/loc1/3EB0ABC.ogg"

    def test_extensao_de_imagem_nao_e_alterada(self, monkeypatch):
        from channels.whatsapp import waha as waha_mod
        monkeypatch.setattr(waha_mod.settings, "public_base_url", "https://app.exemplo.com")
        url = WAHAChannel().public_media_url(
            self._tenant(), "http://localhost:3000/api/files/s1/FOTO.jpeg"
        )
        assert url.endswith("/FOTO.jpeg")

    def test_nao_expoe_o_host_nem_a_chave_do_waha(self, monkeypatch):
        from channels.whatsapp import waha as waha_mod
        monkeypatch.setattr(waha_mod.settings, "public_base_url", "https://app.exemplo.com")
        url = WAHAChannel().public_media_url(
            self._tenant(), "http://localhost:3000/api/files/s1/3EB0ABC.oga"
        )
        assert "SEGREDO" not in url
        assert "waha.exemplo.com" not in url
        assert "localhost" not in url

    def test_sem_public_base_url_devolve_none(self, monkeypatch):
        from channels.whatsapp import waha as waha_mod
        monkeypatch.setattr(waha_mod.settings, "public_base_url", "")
        assert WAHAChannel().public_media_url(
            self._tenant(), "http://localhost:3000/api/files/s1/x.oga"
        ) is None

    def test_url_sem_marcador_de_arquivo(self, monkeypatch):
        from channels.whatsapp import waha as waha_mod
        monkeypatch.setattr(waha_mod.settings, "public_base_url", "https://app.exemplo.com")
        assert WAHAChannel().public_media_url(self._tenant(), "https://x/outro") is None

    def test_none(self):
        assert WAHAChannel().public_media_url(self._tenant(), None) is None


class TestProxySeguranca:
    """O endpoint é público (o GHL não manda header) — validação é a defesa."""

    def _client(self, monkeypatch, tenant=None, waha_resp=None):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from webhooks import media_proxy

        monkeypatch.setattr(media_proxy.token_manager, "get_tenant", lambda loc: tenant)

        class _Resp:
            status_code = 200
            content = b"OGGDATA"
            headers = {"content-type": "audio/ogg"}

        class _AC:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, url, headers=None):
                self.__class__.last_url = url
                self.__class__.last_headers = headers
                return waha_resp or _Resp()

        monkeypatch.setattr(media_proxy.httpx, "AsyncClient", _AC)
        app = FastAPI()
        app.include_router(media_proxy.router)
        return TestClient(app), _AC

    def _tenant_waha(self):
        return SimpleNamespace(
            location_id="loc1abcDEF23456789012", whatsapp_provider="waha",
            waha_session="loc1abcDEF23456789012", zapi_instance_id=None, zapi_token=None,
            waha_base_url="https://waha.exemplo.com", waha_api_key="SEGREDO",
        )

    def test_serve_o_binario_com_chave_em_header(self, monkeypatch):
        t = self._tenant_waha()
        client, AC = self._client(monkeypatch, tenant=t)
        r = client.get(f"/media/whatsapp/{t.location_id}/3EB0ABC.oga")
        assert r.status_code == 200
        assert r.content == b"OGGDATA"
        assert AC.last_headers == {"X-Api-Key": "SEGREDO"}
        # A URL montada usa a sessão do tenant, não input do cliente.
        assert AC.last_url == "https://waha.exemplo.com/api/files/loc1abcDEF23456789012/3EB0ABC.oga"

    def test_ogg_publico_resolve_oga_real_no_waha(self, monkeypatch):
        # O CRM pede .ogg (normalizado); o WAHA só tem .oga. O proxy tenta .ogg,
        # leva 404, e cai no .oga — sem isso o player nunca carregaria.
        from webhooks import media_proxy

        t = self._tenant_waha()
        tentativas = []

        class _AC:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, url, headers=None):
                tentativas.append(url)

                class _R:
                    status_code = 200 if url.endswith(".oga") else 404
                    content = b"OGG" if url.endswith(".oga") else b""
                    headers = {"content-type": "audio/ogg"}
                return _R()

        monkeypatch.setattr(media_proxy.token_manager, "get_tenant", lambda loc: t)
        monkeypatch.setattr(media_proxy.httpx, "AsyncClient", _AC)
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI(); app.include_router(media_proxy.router)
        r = TestClient(app).get(f"/media/whatsapp/{t.location_id}/3EB0ABC.ogg")

        assert r.status_code == 200 and r.content == b"OGG"
        assert [u.split("/")[-1] for u in tentativas] == ["3EB0ABC.ogg", "3EB0ABC.oga"]

    def test_path_traversal_no_filename_bloqueado(self, monkeypatch):
        t = self._tenant_waha()
        client, _ = self._client(monkeypatch, tenant=t)
        # barra vira outro segmento de rota → 404 do próprio router
        assert client.get(f"/media/whatsapp/{t.location_id}/..%2f..%2fapi%2fsessions").status_code == 404

    def test_filename_sem_extensao_bloqueado(self, monkeypatch):
        t = self._tenant_waha()
        client, _ = self._client(monkeypatch, tenant=t)
        assert client.get(f"/media/whatsapp/{t.location_id}/sessions").status_code == 404

    def test_tenant_inexistente_404(self, monkeypatch):
        client, _ = self._client(monkeypatch, tenant=None)
        assert client.get("/media/whatsapp/loc1abcDEF23456789012/x.oga").status_code == 404

    def test_tenant_que_nao_e_waha_404(self, monkeypatch):
        zapi = SimpleNamespace(
            location_id="loc1abcDEF23456789012", whatsapp_provider="zapi",
            waha_session=None, zapi_instance_id="3EB1", zapi_token="tok",
        )
        client, _ = self._client(monkeypatch, tenant=zapi)
        assert client.get(f"/media/whatsapp/{zapi.location_id}/x.oga").status_code == 404

    def test_arquivo_expirado_no_waha_degrada(self, monkeypatch):
        class _R404:
            status_code = 404
            content = b""
            headers = {}
        t = self._tenant_waha()
        client, _ = self._client(monkeypatch, tenant=t, waha_resp=_R404())
        assert client.get(f"/media/whatsapp/{t.location_id}/x.oga").status_code == 404
