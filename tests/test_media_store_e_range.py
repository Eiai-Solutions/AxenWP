"""
Store durável de mídia + Range/CORS do proxy.

O CRM hot-linka a nossa URL e busca preguiçosamente, quando o WAHA já apagou o
arquivo. O binário tem que vir do nosso store, e o <audio> cross-origin só toca
com Range (206) e CORS.
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from webhooks import media_proxy
from webhooks.media_proxy import _parse_range


# ── Range parsing ──

class TestParseRange:
    def test_faixa_normal(self):
        assert _parse_range("bytes=0-99", 1000) == (0, 99)

    def test_faixa_aberta_no_fim(self):
        assert _parse_range("bytes=500-", 1000) == (500, 999)

    def test_sufixo_ultimos_bytes(self):
        assert _parse_range("bytes=-200", 1000) == (800, 999)

    def test_fim_alem_do_total_e_truncado(self):
        assert _parse_range("bytes=0-99999", 1000) == (0, 999)

    def test_inicio_alem_do_total_e_insatisfazivel(self):
        assert _parse_range("bytes=2000-3000", 1000) is None

    def test_lixo_e_rejeitado(self):
        assert _parse_range("linhas=0-9", 1000) is None
        assert _parse_range("bytes=abc", 1000) is None

    def test_multiplas_faixas_nao_suportadas(self):
        assert _parse_range("bytes=0-9,20-29", 1000) is None


# ── Proxy servindo do store ──

class TestProxyServeDoStore:
    def _client(self, monkeypatch, tenant, blob=None):
        monkeypatch.setattr(media_proxy.token_manager, "get_tenant", lambda loc: tenant)

        async def _fake_get_media(loc, fn):
            return blob  # (bytes, content_type) ou None

        monkeypatch.setattr(media_proxy, "get_media", _fake_get_media)
        app = FastAPI()
        app.include_router(media_proxy.router)
        return TestClient(app)

    def _tenant(self):
        return SimpleNamespace(
            location_id="loc1abcDEF23456789012", whatsapp_provider="waha",
            waha_session="loc1abcDEF23456789012", zapi_instance_id=None, zapi_token=None,
            waha_base_url="https://waha.exemplo.com", waha_api_key="K",
        )

    def test_serve_do_store_com_cors_e_accept_ranges(self, monkeypatch):
        t = self._tenant()
        client = self._client(monkeypatch, t, blob=(b"O" * 100, "audio/ogg"))
        r = client.get(f"/media/whatsapp/{t.location_id}/x.ogg")
        assert r.status_code == 200
        assert r.content == b"O" * 100
        assert r.headers["accept-ranges"] == "bytes"
        assert r.headers["access-control-allow-origin"] == "*"
        assert r.headers["content-type"].startswith("audio/ogg")

    def test_range_devolve_206_e_trecho(self, monkeypatch):
        t = self._tenant()
        data = bytes(range(256))
        client = self._client(monkeypatch, t, blob=(data, "audio/ogg"))
        r = client.get(f"/media/whatsapp/{t.location_id}/x.ogg", headers={"Range": "bytes=10-19"})
        assert r.status_code == 206
        assert r.content == data[10:20]
        assert r.headers["content-range"] == "bytes 10-19/256"

    def test_range_insatisfazivel_416(self, monkeypatch):
        t = self._tenant()
        client = self._client(monkeypatch, t, blob=(b"abc", "audio/ogg"))
        r = client.get(f"/media/whatsapp/{t.location_id}/x.ogg", headers={"Range": "bytes=999-"})
        assert r.status_code == 416
        assert r.headers["content-range"] == "bytes */3"

    def test_store_vazio_cai_no_waha_ao_vivo(self, monkeypatch):
        t = self._tenant()
        # sem blob -> tenta WAHA
        class _AC:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, url, headers=None):
                class _R:
                    status_code = 200
                    content = b"LIVE"
                    headers = {"content-type": "audio/ogg"}
                return _R()
        monkeypatch.setattr(media_proxy.httpx, "AsyncClient", _AC)
        client = self._client(monkeypatch, t, blob=None)
        r = client.get(f"/media/whatsapp/{t.location_id}/x.ogg")
        assert r.status_code == 200 and r.content == b"LIVE"
        assert r.headers["accept-ranges"] == "bytes"  # também servido por _serve


# ── media_store: teto de tamanho ──

@pytest.mark.asyncio
async def test_store_recusa_acima_do_teto(monkeypatch):
    from services import media_store

    chamou = {"save": False}

    def _fake_save(*a, **k):
        chamou["save"] = True

    monkeypatch.setattr(media_store, "_save_sync", _fake_save)
    gigante = b"x" * (media_store.MAX_BLOB_BYTES + 1)
    ok = await media_store.store_media("loc", "big.bin", "application/pdf", gigante)
    assert ok is False and chamou["save"] is False


@pytest.mark.asyncio
async def test_store_aceita_dentro_do_teto(monkeypatch):
    from services import media_store

    gravado = {}

    def _fake_save(loc, fn, ct, data):
        gravado.update(loc=loc, fn=fn, ct=ct, n=len(data))

    monkeypatch.setattr(media_store, "_save_sync", _fake_save)
    ok = await media_store.store_media("loc", "voz.ogg", "audio/ogg", b"OGG")
    assert ok is True and gravado == {"loc": "loc", "fn": "voz.ogg", "ct": "audio/ogg", "n": 3}
