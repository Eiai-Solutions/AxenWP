"""Política de exclusividade entre provedores de WhatsApp."""

from types import SimpleNamespace

import pytest

from services.channel_policy import (
    WAHA,
    ZAPI,
    active_whatsapp_provider,
    conflict_message,
    provider_label,
    whatsapp_conflict,
)


def tenant(**kw):
    base = dict(
        whatsapp_provider="zapi",
        waha_session=None,
        zapi_instance_id=None,
        zapi_token=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


# ── provedor ativo ──

def test_tenant_novo_nao_tem_provedor():
    # A migration 022 carimba 'zapi' em todo mundo; sem credencial isso não é um canal.
    assert active_whatsapp_provider(tenant()) is None


def test_waha_ativo_exige_flag_e_sessao():
    assert active_whatsapp_provider(tenant(whatsapp_provider="waha", waha_session="loc123")) == WAHA


def test_flag_waha_sem_sessao_nao_ativa():
    # Estado impossível de servir mensagem — não pode bloquear a Z-API.
    assert active_whatsapp_provider(tenant(whatsapp_provider="waha")) is None


def test_zapi_ativa_exige_instance_e_token():
    assert active_whatsapp_provider(tenant(zapi_instance_id="3EB1", zapi_token="tok")) == ZAPI


def test_zapi_pela_metade_nao_ativa():
    assert active_whatsapp_provider(tenant(zapi_instance_id="3EB1")) is None
    assert active_whatsapp_provider(tenant(zapi_token="tok")) is None


def test_credencial_so_com_espacos_nao_conta():
    assert active_whatsapp_provider(tenant(zapi_instance_id="  ", zapi_token="  ")) is None
    assert active_whatsapp_provider(tenant(whatsapp_provider="waha", waha_session="   ")) is None


def test_waha_vence_zapi_dormente():
    # Credencial Z-API antiga não é apagada na troca — mas não manda mais.
    t = tenant(whatsapp_provider="waha", waha_session="loc123", zapi_instance_id="3EB1", zapi_token="tok")
    assert active_whatsapp_provider(t) == WAHA


def test_flag_case_insensitive():
    assert active_whatsapp_provider(tenant(whatsapp_provider="WAHA", waha_session="loc")) == WAHA


def test_tenant_none():
    assert active_whatsapp_provider(None) is None


def test_tenant_sem_colunas_waha_nao_explode():
    # Tenant carregado de um banco anterior à migration 022.
    legado = SimpleNamespace(zapi_instance_id="3EB1", zapi_token="tok")
    assert active_whatsapp_provider(legado) == ZAPI


# ── conflito ──

def test_sem_provedor_nada_bloqueia():
    assert whatsapp_conflict(tenant(), ZAPI) is None
    assert whatsapp_conflict(tenant(), WAHA) is None


def test_waha_ativo_bloqueia_zapi():
    t = tenant(whatsapp_provider="waha", waha_session="loc123")
    assert whatsapp_conflict(t, ZAPI) == WAHA


def test_zapi_ativa_bloqueia_waha():
    t = tenant(zapi_instance_id="3EB1", zapi_token="tok")
    assert whatsapp_conflict(t, WAHA) == ZAPI


def test_reconfigurar_o_proprio_provedor_e_permitido():
    waha = tenant(whatsapp_provider="waha", waha_session="loc123")
    assert whatsapp_conflict(waha, WAHA) is None
    zapi = tenant(zapi_instance_id="3EB1", zapi_token="tok")
    assert whatsapp_conflict(zapi, ZAPI) is None


# ── mensagem ──

def test_mensagem_de_conflito_nomeia_os_dois_lados():
    msg = conflict_message(WAHA, ZAPI)
    assert "WAHA" in msg and "Z-API" in msg


def test_label():
    assert provider_label(WAHA) == "WAHA"
    assert provider_label(ZAPI) == "Z-API"
    assert provider_label(None) == ""
