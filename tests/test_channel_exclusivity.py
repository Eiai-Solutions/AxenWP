"""
Exclusividade de provedor nas bordas: endpoint de config, connect do WAHA e
webhook de entrada. Bloquear só no card não vale — o form posta direto.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.channel_policy import WAHA, ZAPI, active_whatsapp_provider


def tenant_waha(loc="loc1"):
    return SimpleNamespace(
        location_id=loc,
        whatsapp_provider="waha",
        waha_session=loc,
        zapi_instance_id="3EB1",   # credencial dormente da configuração antiga
        zapi_token="tok",
        zapi_client_token="",
        is_active=True,
    )


def tenant_zapi(loc="loc2"):
    return SimpleNamespace(
        location_id=loc,
        whatsapp_provider="zapi",
        waha_session=None,
        zapi_instance_id="3EB1",
        zapi_token="tok",
        zapi_client_token="",
        is_active=True,
    )


# ── endpoint que salva credenciais Z-API ──

@pytest.mark.asyncio
async def test_salvar_zapi_e_recusado_quando_waha_esta_ativo():
    from admin.dashboard import update_zapi_credentials

    with patch("admin.dashboard.token_manager") as tm:
        tm.get_tenant.return_value = tenant_waha()
        resp = await update_zapi_credentials(
            location_id="loc1", instance_id="X", token="Y", client_token="", authenticated=True
        )

    assert resp.status_code == 303
    assert "err=" in resp.headers["location"]
    assert "tab=canais" in resp.headers["location"]
    tm.update_zapi_credentials.assert_not_called()


@pytest.mark.asyncio
async def test_salvar_zapi_passa_quando_nao_ha_outro_provedor():
    from admin.dashboard import update_zapi_credentials

    livre = SimpleNamespace(
        location_id="loc3", whatsapp_provider="zapi", waha_session=None,
        zapi_instance_id=None, zapi_token=None, zapi_client_token="", is_active=True,
    )
    with patch("admin.dashboard.token_manager") as tm:
        tm.get_tenant.return_value = livre
        resp = await update_zapi_credentials(
            location_id="loc3", instance_id="X", token="Y", client_token="", authenticated=True
        )

    assert resp.status_code == 303
    assert "msg=" in resp.headers["location"]
    tm.update_zapi_credentials.assert_called_once()


@pytest.mark.asyncio
async def test_editar_a_propria_zapi_continua_permitido():
    from admin.dashboard import update_zapi_credentials

    with patch("admin.dashboard.token_manager") as tm:
        tm.get_tenant.return_value = tenant_zapi()
        resp = await update_zapi_credentials(
            location_id="loc2", instance_id="NOVO", token="tok2", client_token="", authenticated=True
        )

    assert "msg=" in resp.headers["location"]
    tm.update_zapi_credentials.assert_called_once()


# ── connect do WAHA ──

@pytest.mark.asyncio
async def test_connect_waha_recusa_quando_zapi_esta_ativa():
    from admin.waha import waha_connect

    with patch("admin.waha.token_manager") as tm:
        tm.get_tenant.return_value = tenant_zapi()
        out = await waha_connect(location_id="loc2", authenticated=True)

    assert out["conflict"] == ZAPI
    assert "Z-API" in out["error"]


@pytest.mark.asyncio
async def test_connect_waha_com_force_prossegue():
    from admin.waha import waha_connect

    with patch("admin.waha.token_manager") as tm, \
         patch("admin.waha._global_cfg", return_value=("https://waha.example", "key")), \
         patch("admin.waha.waha_service") as svc, \
         patch("admin.waha.SessionLocal") as session_local:
        tm.get_tenant.return_value = tenant_zapi()
        svc.create_session = AsyncMock(return_value=True)
        svc.get_session = AsyncMock(return_value={"status": "STARTING"})
        db = MagicMock()
        session_local.return_value = db
        row = SimpleNamespace(whatsapp_provider="zapi", waha_session=None)
        db.query.return_value.filter.return_value.first.return_value = row

        out = await waha_connect(location_id="loc2", force=True, authenticated=True)

    assert out["success"] is True
    # A troca marca o WAHA sem apagar a credencial antiga.
    assert row.whatsapp_provider == "waha"
    assert row.waha_session == "loc2"


@pytest.mark.asyncio
async def test_connect_nao_marca_waha_se_a_sessao_nao_foi_criada():
    """Servidor WAHA fora do ar não pode derrubar o provedor que estava funcionando."""
    from admin.waha import waha_connect

    with patch("admin.waha.token_manager") as tm, \
         patch("admin.waha._global_cfg", return_value=("https://waha.example", "key")), \
         patch("admin.waha.waha_service") as svc, \
         patch("admin.waha.SessionLocal") as session_local:
        tm.get_tenant.return_value = tenant_zapi()
        svc.create_session = AsyncMock(return_value=None)   # servidor recusou

        out = await waha_connect(location_id="loc2", force=True, authenticated=True)

    assert "error" in out
    session_local.assert_not_called()   # nada foi gravado no tenant


@pytest.mark.asyncio
async def test_disconnect_libera_mesmo_sem_servidor_global():
    """Trocar a config global do admin não pode prender a instância em WAHA."""
    from admin.waha import waha_session_action

    with patch("admin.waha.token_manager") as tm, \
         patch("admin.waha._global_cfg", return_value=(None, None)), \
         patch("admin.waha.SessionLocal") as session_local:
        tm.get_tenant.return_value = tenant_waha()
        db = MagicMock()
        session_local.return_value = db
        row = SimpleNamespace(whatsapp_provider="waha", waha_session="loc1")
        db.query.return_value.filter.return_value.first.return_value = row

        out = await waha_session_action(location_id="loc1", action="disconnect", authenticated=True)

    assert out["success"] is True
    assert row.waha_session is None
    assert row.whatsapp_provider == "zapi"


@pytest.mark.asyncio
async def test_disconnect_libera_o_provedor():
    from admin.waha import waha_session_action

    with patch("admin.waha.token_manager") as tm, \
         patch("admin.waha._global_cfg", return_value=("https://waha.example", "key")), \
         patch("admin.waha.waha_service") as svc, \
         patch("admin.waha.SessionLocal") as session_local:
        tm.get_tenant.return_value = tenant_waha()
        svc.logout_session = AsyncMock(return_value=True)
        svc.delete_session = AsyncMock(return_value=True)
        db = MagicMock()
        session_local.return_value = db
        row = SimpleNamespace(whatsapp_provider="waha", waha_session="loc1")
        db.query.return_value.filter.return_value.first.return_value = row

        out = await waha_session_action(location_id="loc1", action="disconnect", authenticated=True)

    assert out["success"] is True
    assert row.waha_session is None
    assert active_whatsapp_provider(row) is None or row.whatsapp_provider == "zapi"


@pytest.mark.asyncio
async def test_logout_nao_libera_o_provedor():
    """Queda temporária de sessão não pode destravar a Z-API no meio de um reboot."""
    from admin.waha import waha_session_action

    with patch("admin.waha.token_manager") as tm, \
         patch("admin.waha._global_cfg", return_value=("https://waha.example", "key")), \
         patch("admin.waha.waha_service") as svc, \
         patch("admin.waha.SessionLocal") as session_local:
        tm.get_tenant.return_value = tenant_waha()
        svc.logout_session = AsyncMock(return_value=True)
        db = MagicMock()
        session_local.return_value = db

        await waha_session_action(location_id="loc1", action="logout", authenticated=True)

    session_local.assert_not_called()


# ── saída do CRM (GHL -> WhatsApp) ──

@pytest.mark.asyncio
async def test_outbound_do_ghl_nao_sai_pela_zapi_dormente():
    """A exclusividade vale nos dois sentidos: entrada cortada, saída também."""
    from webhooks import ghl_provider

    payload = SimpleNamespace(
        status="pending", messageId="msg1", phone="5547999", contactId="c1",
        locationId="loc1", message="oi", attachments=[], type="SMS",
    )
    with patch.object(ghl_provider, "token_manager") as tm, \
         patch.object(ghl_provider, "ghl_service") as ghl, \
         patch.object(ghl_provider, "zapi_service") as zapi:
        tm.get_tenant.return_value = tenant_waha()
        ghl.update_message_status = AsyncMock()
        await ghl_provider.process_outbound_message(payload)

    zapi.send_text.assert_not_called()
    # O operador precisa VER que não saiu — falha explícita no CRM, não silêncio.
    ghl.update_message_status.assert_awaited_once()
    assert ghl.update_message_status.await_args.kwargs["status"] == "failed"


# ── webhook de entrada da Z-API ──

@pytest.mark.asyncio
async def test_inbound_zapi_ignorado_quando_instancia_usa_waha():
    """Webhook velho da Z-API não pode responder junto com o WAHA no mesmo número."""
    from webhooks import zapi_receiver

    with patch.object(zapi_receiver, "token_manager") as tm, \
         patch.object(zapi_receiver, "ZAPIChannel") as channel:
        tm.get_tenant.return_value = tenant_waha()
        await zapi_receiver.process_inbound_message("loc1", {"phone": "5547999", "text": {"message": "oi"}})

    channel.assert_not_called()


@pytest.mark.asyncio
async def test_inbound_zapi_processa_quando_zapi_e_o_provedor():
    from webhooks import zapi_receiver

    with patch.object(zapi_receiver, "token_manager") as tm, \
         patch.object(zapi_receiver, "ZAPIChannel") as channel:
        tm.get_tenant.return_value = tenant_zapi()
        channel.return_value.parse_inbound.return_value = SimpleNamespace(
            sender_id="5547999", message_type="text", is_group=True,  # corta cedo, de propósito
            from_me=False, provider_message_id="X", text="oi",
        )
        await zapi_receiver.process_inbound_message("loc2", {"phone": "5547999"})

    channel.return_value.parse_inbound.assert_called_once()
