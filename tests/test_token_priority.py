"""
Prioridade de token: o do APP (OAuth) na frente do PIT.

Motivo, verificado contra o CRM real: `PUT /conversations/messages/{id}/status`
devolve 401 `CONVERSATIONS_MSG_PROVIDER_NO_ACCESS` para token que não pertence
ao app dono do conversation provider. Com o PIT na frente, todo status de
entrega que reportávamos falhava calado — a leitura da mesma mensagem passava,
o que tornava o problema invisível.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from auth.token_manager import token_manager


def tenant(**kw):
    futuro = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
    base = dict(
        location_id="loc1",
        company_name="Empresa",
        pit_token=None,
        access_token=None,
        refresh_token=None,
        token_expires_at=futuro,
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_oauth_vence_o_pit_quando_os_dois_existem():
    t = tenant(pit_token="PIT", access_token="OAUTH", refresh_token="R")
    with patch.object(token_manager, "get_tenant", return_value=t):
        assert await token_manager.get_valid_token("loc1") == "OAUTH"


@pytest.mark.asyncio
async def test_pit_sozinho_continua_valendo():
    # Tenant que nunca instalou o app — é o caso da maioria hoje.
    t = tenant(pit_token="PIT")
    with patch.object(token_manager, "get_tenant", return_value=t):
        assert await token_manager.get_valid_token("loc1") == "PIT"


@pytest.mark.asyncio
async def test_oauth_expirado_e_renovado_antes_de_usar():
    passado = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    velho = tenant(access_token="ANTIGO", refresh_token="R", token_expires_at=passado)
    novo = tenant(access_token="NOVO", refresh_token="R")

    with patch.object(token_manager, "get_tenant", side_effect=[velho, novo]), \
         patch.object(token_manager, "_refresh_token", AsyncMock(return_value=True)):
        assert await token_manager.get_valid_token("loc1") == "NOVO"


@pytest.mark.asyncio
async def test_refresh_falhou_mas_ha_pit_nao_perde_o_acesso():
    # Degradar para o PIT é ruim (status não sobe), mas melhor que ficar sem token.
    passado = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    t = tenant(pit_token="PIT", access_token="ANTIGO", refresh_token="R", token_expires_at=passado)

    with patch.object(token_manager, "get_tenant", return_value=t), \
         patch.object(token_manager, "_refresh_token", AsyncMock(return_value=False)):
        assert await token_manager.get_valid_token("loc1") == "PIT"


@pytest.mark.asyncio
async def test_refresh_falhou_e_sem_pit_devolve_none():
    passado = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    t = tenant(access_token="ANTIGO", refresh_token="R", token_expires_at=passado)

    with patch.object(token_manager, "get_tenant", return_value=t), \
         patch.object(token_manager, "_refresh_token", AsyncMock(return_value=False)):
        assert await token_manager.get_valid_token("loc1") is None


@pytest.mark.asyncio
async def test_sem_token_nenhum():
    with patch.object(token_manager, "get_tenant", return_value=tenant()):
        assert await token_manager.get_valid_token("loc1") is None


def test_has_oauth():
    assert token_manager._has_oauth(tenant(access_token="A")) is True
    assert token_manager._has_oauth(tenant(refresh_token="R")) is True
    assert token_manager._has_oauth(tenant(pit_token="PIT")) is False


@pytest.mark.asyncio
async def test_refresh_que_falhou_nao_e_retentado_a_cada_chamada():
    """
    A Inhance tem OAuth expirado desde abril e PIT vivo. Sem cooldown, cada
    mensagem pagaria um round-trip perdido de refresh no caminho do webhook.
    """
    passado = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    t = tenant(pit_token="PIT", access_token="ANTIGO", refresh_token="R", token_expires_at=passado)
    refresh = AsyncMock(return_value=False)

    token_manager._refresh_falhou_em.clear()
    with patch.object(token_manager, "get_tenant", return_value=t), \
         patch.object(token_manager, "_refresh_token", refresh):
        for _ in range(5):
            assert await token_manager.get_valid_token("loc1") == "PIT"

    assert refresh.await_count == 1   # e não 5
    token_manager._refresh_falhou_em.clear()


@pytest.mark.asyncio
async def test_cooldown_expira_e_volta_a_tentar():
    """Se o operador reinstalar o app, voltamos ao OAuth sozinhos."""
    passado = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    velho = tenant(pit_token="PIT", access_token="ANTIGO", refresh_token="R", token_expires_at=passado)
    novo = tenant(pit_token="PIT", access_token="NOVO", refresh_token="R")

    token_manager._refresh_falhou_em.clear()
    with patch.object(token_manager, "get_tenant", return_value=velho), \
         patch.object(token_manager, "_refresh_token", AsyncMock(return_value=False)):
        assert await token_manager.get_valid_token("loc1") == "PIT"

    # Simula o fim da janela sem depender de relógio real.
    token_manager._refresh_falhou_em["loc1"] -= token_manager._REFRESH_COOLDOWN_SEGUNDOS + 1

    with patch.object(token_manager, "get_tenant", side_effect=[velho, novo]), \
         patch.object(token_manager, "_refresh_token", AsyncMock(return_value=True)):
        assert await token_manager.get_valid_token("loc1") == "NOVO"

    assert "loc1" not in token_manager._refresh_falhou_em
