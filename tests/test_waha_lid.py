"""
Resolução de identidade @lid -> telefone no inbound do WAHA.

O motor GOWS entrega o remetente como LID (identidade privada do WhatsApp).
Sem resolver, o contato nasce no CRM sem telefone ("Lead do WhatsApp (Anúncio)")
e a resposta digitada pelo operador não tem para onde ir — foi exatamente o que
aconteceu em produção:

    [WAHA] inbound de=198101675561023@lid
    Mensagem inbound registrada no GHL: phone=+198101675561023@lid
    GHL Outbound abortado: Telefone não encontrado para o contato YIfNiRpSM5ulpHJpu6Pn

O caminho normal resolve SEM I/O: o próprio payload traz `_data.Info.SenderAlt`.
"""

import time
from types import SimpleNamespace

import pytest

from channels.whatsapp.waha import WAHAChannel, _phone_from_jid
from services import waha_service as waha_mod


def _parse(payload_inner):
    return WAHAChannel().parse_inbound("loc1", {"event": "message", "payload": payload_inner})


# Payload real capturado da instância de produção (GOWS 2026.6.1).
PAYLOAD_REAL = {
    "id": "false_198101675561023@lid_3EB036E5C9479FC8FC496F",
    "from": "198101675561023@lid",
    "fromMe": False,
    "body": "oi",
    "hasMedia": False,
    "_data": {
        "Info": {
            "Chat": "198101675561023@lid",
            "Sender": "198101675561023:77@lid",
            "SenderAlt": "554797838884:77@s.whatsapp.net",
            "PushName": "Luiz Antonio",
            "IsFromMe": False,
            "IsGroup": False,
        }
    },
}


class TestPhoneFromJid:
    def test_extrai_telefone_descartando_device_e_sufixo(self):
        assert _phone_from_jid("554797838884:77@s.whatsapp.net") == "554797838884"

    def test_aceita_c_us(self):
        assert _phone_from_jid("5511999998888@c.us") == "5511999998888"

    def test_lid_nao_e_telefone(self):
        # O número de um LID não é discável — devolver isso seria pior que None.
        assert _phone_from_jid("198101675561023@lid") is None
        assert _phone_from_jid("198101675561023:77@lid") is None

    def test_vazio(self):
        assert _phone_from_jid("") is None
        assert _phone_from_jid("semdigitos@c.us") is None


class TestParseResolveLid:
    def test_payload_de_producao_vira_telefone_real(self):
        pm = _parse(PAYLOAD_REAL)
        assert pm.sender_id == "554797838884"
        assert pm.sender_lid == "198101675561023@lid"

    def test_pushname_vira_nome_quando_notifyname_falta(self):
        # Sem isso o contato entra como "Lead do WhatsApp (Anúncio)".
        pm = _parse(PAYLOAD_REAL)
        assert pm.sender_name == "Luiz Antonio"

    def test_notifyname_tem_prioridade(self):
        p = {**PAYLOAD_REAL, "notifyName": "Nome do Contato"}
        assert _parse(p).sender_name == "Nome do Contato"

    def test_sem_sender_alt_mantem_lid(self):
        # Fallback: comportamento antigo preservado, resolução fica com o receiver.
        p = {"from": "12345@lid", "body": "x", "_data": {"Info": {"PushName": "X"}}}
        pm = _parse(p)
        assert pm.sender_id == "12345@lid"
        assert pm.sender_lid is None

    def test_sender_alt_vazio_mantem_lid(self):
        p = {"from": "12345@lid", "body": "x", "_data": {"Info": {"SenderAlt": ""}}}
        assert _parse(p).sender_id == "12345@lid"

    def test_sender_alt_que_e_outro_lid_nao_engana(self):
        p = {"from": "12345@lid", "body": "x", "_data": {"Info": {"SenderAlt": "999@lid"}}}
        assert _parse(p).sender_id == "12345@lid"

    def test_contato_normal_nao_paga_nada(self):
        pm = _parse({"from": "5511999998888@c.us", "body": "oi"})
        assert pm.sender_id == "5511999998888"
        assert pm.sender_lid is None

    def test_grupo_continua_marcado(self):
        pm = _parse({"from": "12345@g.us", "body": "x"})
        assert pm.is_group is True

    def test_payload_sem_data_nao_explode(self):
        assert _parse({"from": "5511@c.us", "body": "x"}).sender_id == "5511"


class TestResolveLidFallback:
    """Fallback HTTP: só entra quando o payload não trouxe SenderAlt."""

    @pytest.fixture(autouse=True)
    def _limpa_cache(self):
        waha_mod._lid_cache.clear()
        yield
        waha_mod._lid_cache.clear()

    @pytest.mark.asyncio
    async def test_resolve_e_cacheia(self, monkeypatch):
        chamadas = []

        async def fake_get(base, key, path, *, timeout=None):
            chamadas.append(path)
            return SimpleNamespace(status_code=200, json=lambda: {"pn": "554797838884@c.us"})

        monkeypatch.setattr(waha_mod.waha_service, "_get", fake_get)

        svc = waha_mod.waha_service
        assert await svc.resolve_lid("http://w", "k", "s1", "198@lid") == "554797838884"
        assert await svc.resolve_lid("http://w", "k", "s1", "198@lid") == "554797838884"
        assert len(chamadas) == 1  # segunda veio do cache

    @pytest.mark.asyncio
    async def test_falha_devolve_none_quando_nunca_resolveu(self, monkeypatch):
        async def fake_get(base, key, path, *, timeout=None):
            return SimpleNamespace(status_code=404, json=lambda: {})

        monkeypatch.setattr(waha_mod.waha_service, "_get", fake_get)
        assert await waha_mod.waha_service.resolve_lid("http://w", "k", "s1", "x@lid") is None

    @pytest.mark.asyncio
    async def test_falha_apos_sucesso_serve_o_valor_velho(self, monkeypatch):
        """
        O furo que o revisor apontou: se a resolução oscilar, a mesma pessoa vira
        dois contatos no CRM e duas janelas de debounce. Positivo velho > nada.
        """
        svc = waha_mod.waha_service

        async def ok(base, key, path, *, timeout=None):
            return SimpleNamespace(status_code=200, json=lambda: {"pn": "554797838884@c.us"})

        monkeypatch.setattr(svc, "_get", ok)
        assert await svc.resolve_lid("http://w", "k", "s1", "198@lid") == "554797838884"

        # Expira a entrada e derruba a rede.
        chave = ("http://w", "s1", "198@lid")
        waha_mod._lid_cache[chave] = (time.monotonic() - waha_mod._LID_TTL - 1, "554797838884")

        async def falha(base, key, path, *, timeout=None):
            return None

        monkeypatch.setattr(svc, "_get", falha)
        assert await svc.resolve_lid("http://w", "k", "s1", "198@lid") == "554797838884"

    @pytest.mark.asyncio
    async def test_cache_nao_cruza_entre_sessoes(self, monkeypatch):
        """Dois tenants dividem o servidor WAHA; a sessão é o que isola."""
        async def fake_get(base, key, path, *, timeout=None):
            fone = "111111111111" if "/s1/" in path else "222222222222"
            return SimpleNamespace(status_code=200, json=lambda: {"pn": f"{fone}@c.us"})

        monkeypatch.setattr(waha_mod.waha_service, "_get", fake_get)
        svc = waha_mod.waha_service
        assert await svc.resolve_lid("http://w", "k", "s1", "198@lid") == "111111111111"
        assert await svc.resolve_lid("http://w", "k", "s2", "198@lid") == "222222222222"

    @pytest.mark.asyncio
    async def test_sem_config_nao_chama_rede(self, monkeypatch):
        async def nunca(*a, **kw):
            raise AssertionError("não deveria chamar a rede")

        monkeypatch.setattr(waha_mod.waha_service, "_get", nunca)
        assert await waha_mod.waha_service.resolve_lid("", "k", "s1", "198@lid") is None
        assert await waha_mod.waha_service.resolve_lid("http://w", "k", "", "198@lid") is None

    @pytest.mark.asyncio
    async def test_cap_do_cache(self, monkeypatch):
        async def fake_get(base, key, path, *, timeout=None):
            return SimpleNamespace(status_code=200, json=lambda: {"pn": "551199999999@c.us"})

        monkeypatch.setattr(waha_mod.waha_service, "_get", fake_get)
        monkeypatch.setattr(waha_mod, "_LID_CACHE_MAX", 3)
        for i in range(6):
            await waha_mod.waha_service.resolve_lid("http://w", "k", "s1", f"{i}@lid")
        assert len(waha_mod._lid_cache) <= 3
