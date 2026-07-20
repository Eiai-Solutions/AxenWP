"""
Rota de entrada do WAHA + pipeline compartilhado.

A rota é a que a sessão do WAHA já tem registrada em produção; enquanto ela não
existiu, todo inbound do WhatsApp caiu em 404.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from channels.whatsapp.waha import WAHAChannel
from services import inbound_pipeline


def tenant(mode="ghl", **kw):
    base = dict(
        location_id="loc1",
        company_name="Eiai",
        mode=mode,
        whatsapp_provider="waha",
        waha_session="loc1",
        zapi_instance_id=None,
        zapi_token=None,
        conversation_provider_id=None,
        is_active=True,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def evento_mensagem(**kw):
    p = dict(id="msg-1", **{"from": "554797204869@c.us"}, body="oi", fromMe=False,
             hasMedia=False, notifyName="Cliente")
    p.update(kw)
    return {"event": "message", "session": "loc1", "payload": p}


# ── rota ──

def test_rota_existe_e_esta_registrada():
    import main

    rotas = [r.path for r in main.app.routes]
    assert "/webhook/whatsapp/{location_id}" in rotas


@pytest.mark.asyncio
async def test_evento_de_mensagem_vai_para_o_pipeline():
    from webhooks import waha_receiver

    with patch.object(waha_receiver, "token_manager") as tm, \
         patch.object(waha_receiver, "handle_inbound", new=AsyncMock()) as handle:
        tm.get_tenant.return_value = tenant()
        await waha_receiver.process_waha_message("loc1", evento_mensagem())

    handle.assert_awaited_once()
    pm = handle.await_args.args[2]
    assert pm.sender_id == "554797204869"
    assert pm.text == "oi"


@pytest.mark.asyncio
async def test_inbound_recusado_quando_a_instancia_nao_usa_waha():
    """Sessão órfã de uma instância que voltou para a Z-API não pode conversar."""
    from webhooks import waha_receiver

    zapi = tenant(whatsapp_provider="zapi", waha_session=None,
                  zapi_instance_id="3EB1", zapi_token="tok")
    with patch.object(waha_receiver, "token_manager") as tm, \
         patch.object(waha_receiver, "handle_inbound", new=AsyncMock()) as handle:
        tm.get_tenant.return_value = zapi
        await waha_receiver.process_waha_message("loc1", evento_mensagem())

    handle.assert_not_awaited()


@pytest.mark.asyncio
async def test_instancia_pausada_nao_processa():
    from webhooks import waha_receiver

    with patch.object(waha_receiver, "token_manager") as tm, \
         patch.object(waha_receiver, "handle_inbound", new=AsyncMock()) as handle:
        tm.get_tenant.return_value = tenant(is_active=False)
        await waha_receiver.process_waha_message("loc1", evento_mensagem())

    handle.assert_not_awaited()


# ── filtros do pipeline ──

@pytest.mark.asyncio
async def test_pipeline_ignora_grupo_proprio_e_eco():
    ch = WAHAChannel()
    t = tenant(mode="whatsapp_only")

    async def roda(payload):
        pm = ch.parse_inbound("loc1", payload)
        with patch.object(inbound_pipeline, "ai_is_enabled", new=AsyncMock(return_value=True)), \
             patch.object(inbound_pipeline, "_agendar_ia") as agendar:
            await inbound_pipeline.handle_inbound(ch, t, pm)
        return agendar

    grupo = await roda(evento_mensagem(**{"from": "12345@g.us"}))
    grupo.assert_not_called()

    propria = await roda(evento_mensagem(fromMe=True))
    propria.assert_not_called()

    vazia = await roda(evento_mensagem(body=""))
    vazia.assert_not_called()

    inbound_pipeline.track_sent_message("msg-eco")
    eco = await roda(evento_mensagem(id="msg-eco"))
    eco.assert_not_called()

    normal = await roda(evento_mensagem(id="msg-nova"))
    normal.assert_called_once()


@pytest.mark.asyncio
async def test_dedup_impede_o_agente_de_responder_ao_proprio_eco():
    """O WAHA reentrega o que enviamos; sem dedup o agente conversaria sozinho."""
    inbound_pipeline.track_sent_message("enviada-por-nos")
    assert inbound_pipeline.was_sent_by_us("enviada-por-nos") is True
    assert inbound_pipeline.was_sent_by_us("de-outra-pessoa") is False


def test_cleanup_expira_ids_antigos():
    inbound_pipeline._sent_message_ids.clear()
    inbound_pipeline.track_sent_message("velha")
    inbound_pipeline._sent_message_ids["velha"] = 0.0  # muito antiga
    inbound_pipeline.track_sent_message("nova")

    inbound_pipeline.cleanup_stale_entries()

    assert inbound_pipeline.was_sent_by_us("velha") is False
    assert inbound_pipeline.was_sent_by_us("nova") is True


def test_split_chunks():
    assert inbound_pipeline.split_chunks("a\n\nb") == ["a", "b"]
    assert inbound_pipeline.split_chunks("uma linha só") == ["uma linha só"]
    assert inbound_pipeline.split_chunks("") == []


# ── chatId ──

def test_chat_id_normaliza_telefone_formatado():
    """Telefone do CRM vem '+55 47 99720-4869'; o WAHA aceita e a msg some."""
    ch = WAHAChannel()
    assert ch._chat_id("+55 47 99720-4869") == "5547997204869@c.us"
    assert ch._chat_id("5547997204869") == "5547997204869@c.us"
    assert ch._chat_id("5547997204869@c.us") == "5547997204869@c.us"
    assert ch._chat_id("76412417495205@lid") == "76412417495205@lid"


# ── turno da IA ──

@pytest.mark.asyncio
async def test_resposta_da_ia_sai_pelo_adapter_e_o_id_vira_dedup():
    ch = WAHAChannel()
    t = tenant(mode="whatsapp_only")
    pm = ch.parse_inbound("loc1", evento_mensagem(id="entrada-1"))

    enviados = []

    async def send_text(tenant_, to, texto, *, typing_delay=0):
        enviados.append((to, texto))
        return SimpleNamespace(ok=True, provider_message_id=f"saida-{len(enviados)}")

    adapter = SimpleNamespace(provider="waha", send_text=send_text, send_audio=AsyncMock())
    inbound_pipeline._message_buffers["loc1:554797204869"] = [("oi", False, None)]
    inbound_pipeline._debounce_config["loc1:554797204869"] = 0.0

    fake_ai = SimpleNamespace(process_incoming_message=AsyncMock(
        return_value={"type": "text", "content": "Olá!\n\nComo posso ajudar?"}
    ))
    with patch.dict("sys.modules", {"services.ai_service": SimpleNamespace(ai_service=fake_ai)}):
        await inbound_pipeline._run_ai(adapter, t, pm, None, "loc1:554797204869")

    assert [t_ for _, t_ in enviados] == ["Olá!", "Como posso ajudar?"]
    # Os ids do que enviamos entram no dedup, senão o eco do WAHA reinicia o ciclo.
    assert inbound_pipeline.was_sent_by_us("saida-1")
    assert inbound_pipeline.was_sent_by_us("saida-2")


@pytest.mark.asyncio
async def test_falha_de_espelho_no_crm_nao_deixa_o_cliente_sem_resposta():
    ch = WAHAChannel()
    t = tenant(mode="ghl")
    pm = ch.parse_inbound("loc1", evento_mensagem(id="msg-crm"))

    with patch.object(inbound_pipeline, "resolve_contact_id", new=AsyncMock(return_value="c1")), \
         patch.object(inbound_pipeline, "mirror_inbound", new=AsyncMock(return_value=None)), \
         patch.object(inbound_pipeline, "ai_is_enabled", new=AsyncMock(return_value=True)), \
         patch.object(inbound_pipeline, "_agendar_ia") as agendar:
        await inbound_pipeline.handle_inbound(ch, t, pm)

    agendar.assert_called_once()


@pytest.mark.asyncio
async def test_sem_contato_no_crm_o_turno_para():
    ch = WAHAChannel()
    t = tenant(mode="ghl")
    pm = ch.parse_inbound("loc1", evento_mensagem(id="msg-sem-contato"))

    with patch.object(inbound_pipeline, "resolve_contact_id", new=AsyncMock(return_value=None)), \
         patch.object(inbound_pipeline, "_agendar_ia") as agendar:
        await inbound_pipeline.handle_inbound(ch, t, pm)

    agendar.assert_not_called()
