"""
O que a gente pede à Fish Audio no TTS.

Estes testes existem porque o request estava incompleto e ninguém veria: o áudio
saía, só que pior. Faltavam `latency` e `max_new_tokens`, e os dois só aparecem
como qualidade — nunca como erro.

Medições de 2026-08-19 que sustentam as escolhas:
  · `latency=balanced` entrega 18,5s onde `normal` entrega 20,0s na mesma frase.
  · o corte da frase final acontece em ~8% das gerações (11/12 completas), e não é
    o transporte: o Opus vem mono/48kHz com fim-de-stream marcado, e o WAHA manda
    com `convert:false`.
"""

import asyncio

import pytest

import services.audio_handler as ah


class _Resp:
    status_code = 200
    content = b"OggS-fake"
    text = ""


def _espiao(monkeypatch):
    """Captura o corpo e os headers do POST, sem sair para a rede."""
    visto = {}

    class _AC:
        def __init__(self, **kw):
            visto["client_kwargs"] = kw

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            visto["url"] = url
            visto["headers"] = headers or {}
            visto["json"] = json or {}
            return _Resp()

    monkeypatch.setattr(ah.httpx, "AsyncClient", _AC, raising=True)
    return visto


def _gerar(**kw):
    return asyncio.run(ah.synthesize_speech_fishaudio(
        text=kw.pop("text", "oi"), api_key="k", voice_id="v" * 32, **kw))


def test_manda_latency_normal(monkeypatch):
    """
    `normal` é o default do servidor hoje. Mandar explícito não muda o áudio —
    muda quem decide: um default alterado do lado deles degradaria a voz aqui
    sem nenhum sinal, e ninguém ligaria uma coisa na outra.
    """
    visto = _espiao(monkeypatch)
    _gerar()
    assert visto["json"]["latency"] == "normal"


def test_nunca_pede_o_modo_rapido(monkeypatch):
    """`low`/`balanced` correm e perdem prosódia — é o oposto do que foi pedido."""
    visto = _espiao(monkeypatch)
    _gerar()
    assert visto["json"]["latency"] not in ("low", "balanced")


def test_manda_folga_de_tokens(monkeypatch):
    """Teto, não meta: o default 1024 é a única causa documentada de parada dura."""
    visto = _espiao(monkeypatch)
    _gerar()
    assert visto["json"]["max_new_tokens"] >= 2048


def test_a_engine_vai_no_HEADER_e_nao_no_corpo(monkeypatch):
    """
    Na API da Fish a engine é header (`model:`), não campo do JSON. Mandar no
    corpo seria ignorado em silêncio — o s2-pro que o dono escolheu nunca valeria,
    e o áudio continuaria saindo, só que do s1.
    """
    visto = _espiao(monkeypatch)
    _gerar(model="s2-pro")
    assert visto["headers"]["model"] == "s2-pro"
    assert "model" not in visto["json"]


def test_o_resto_do_contrato_nao_se_perdeu(monkeypatch):
    visto = _espiao(monkeypatch)
    _gerar(text="frase", speed=0.95, temperature=0.5, top_p=0.6, normalize_loudness=True)
    c = visto["json"]
    assert c["text"] == "frase"
    assert c["format"] == "opus", "WhatsApp toca nota de voz em Ogg/Opus"
    assert c["temperature"] == 0.5 and c["top_p"] == 0.6
    assert c["prosody"] == {"speed": 0.95, "normalize_loudness": True}


@pytest.mark.parametrize("entrada,esperado", [(3.0, 2.0), (0.1, 0.5), (None, 1.0)])
def test_velocidade_fica_na_faixa_da_api(monkeypatch, entrada, esperado):
    visto = _espiao(monkeypatch)
    _gerar(speed=entrada)
    assert visto["json"]["prosody"]["speed"] == esperado


def test_o_log_de_uso_grava_QUAL_engine_falou(monkeypatch):
    """
    Sem a engine na linha, s1 e s2-pro somam no mesmo balde e a conta some.

    Medido na conta do tenant em 2026-08-19: s2-pro custa $30 por 1M de
    caracteres e s1 não consumiu crédito nenhum. Uma diferença dessas não pode
    depender de alguém lembrar qual engine estava ligada naquele dia.
    """
    _espiao(monkeypatch)
    registrado = {}

    def _log(**kw):
        registrado.update(kw)

    monkeypatch.setattr(ah, "save_usage_log", _log, raising=True)
    _gerar(text="frase de teste", model="s2-pro", location_id="loc1")

    assert registrado.get("service") == "fishaudio"
    assert registrado.get("model") == "s2-pro", "log sem a engine: s1 e s2-pro viram um número só"
    assert registrado.get("characters") == len("frase de teste")
    assert registrado.get("location_id") == "loc1"


def test_sem_location_id_nao_grava_uso(monkeypatch):
    """Sem tenant não há onde pendurar — a FK exige `tenants.location_id`."""
    _espiao(monkeypatch)
    chamou = []
    monkeypatch.setattr(ah, "save_usage_log", lambda **kw: chamou.append(kw), raising=True)
    _gerar()
    assert chamou == []


def test_timeout_cabe_no_audio_longo(monkeypatch):
    """
    Uma frase de 300 chars leva ~20s para gerar. Com o timeout de 30s antigo, a
    resposta lenta virava exceção e o lead simplesmente não recebia áudio —
    silêncio, não erro visível.
    """
    visto = _espiao(monkeypatch)
    _gerar()
    assert visto["client_kwargs"]["timeout"] >= 60
