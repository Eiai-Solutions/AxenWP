"""
Fase 1 do painel de chat: o log precisa contar a verdade antes de virar tela.

Dois buracos que a tela exporia na cara do cliente:

1. O WAHA assina `message.ack` mas o receiver descartava o evento no `else` — o
   status congelava em "sent" e o tique nunca virava "lido".
2. O áudio que a IA envia era gravado só como o rótulo "[Áudio da IA]", sem
   referência de mídia: a bolha apareceria muda, sem player, e o cliente não teria
   como ouvir o que o próprio agente dele falou.
"""

import base64

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from data.models import Base, Message

LOC = "loc1"


@pytest.fixture
def db(monkeypatch, tmp_path):
    from services import message_log

    engine = create_engine(f"sqlite:///{tmp_path}/ack.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(message_log, "SessionLocal", Session)
    return Session


# --------------------------------------------------------------------------- #
# Progressão do status — acks chegam fora de ordem
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_status_avanca_ate_lido(db):
    from services.message_log import persist_message, update_message_status

    await persist_message(
        location_id=LOC, channel="whatsapp", direction="outbound", sender_role="ai",
        contact_ref="5547999", text="oi", provider_message_id="WA1", status="sent",
    )
    for novo in ("delivered", "read"):
        await update_message_status(LOC, provider_message_id="WA1", status=novo)

    s = db()
    assert s.query(Message).filter_by(provider_message_id="WA1").first().status == "read"
    s.close()


@pytest.mark.asyncio
async def test_ack_atrasado_nao_rebaixa_o_status(db):
    """
    O WhatsApp entrega ack fora de ordem e o WAHA reentrega. Sem guarda, um "sent"
    tardio sobrescreveria um "read" já registrado e o tique passaria a mentir.
    """
    from services.message_log import persist_message, update_message_status

    await persist_message(
        location_id=LOC, channel="whatsapp", direction="outbound", sender_role="ai",
        contact_ref="5547999", text="oi", provider_message_id="WA2", status="sent",
    )
    await update_message_status(LOC, provider_message_id="WA2", status="read")
    await update_message_status(LOC, provider_message_id="WA2", status="sent")      # atrasado
    await update_message_status(LOC, provider_message_id="WA2", status="delivered")  # atrasado

    s = db()
    assert s.query(Message).filter_by(provider_message_id="WA2").first().status == "read"
    s.close()


@pytest.mark.asyncio
async def test_falha_sempre_vale_e_nao_e_desfeita_por_ack(db):
    from services.message_log import persist_message, update_message_status

    await persist_message(
        location_id=LOC, channel="whatsapp", direction="outbound", sender_role="ai",
        contact_ref="5547999", text="oi", provider_message_id="WA3", status="delivered",
    )
    await update_message_status(LOC, provider_message_id="WA3", status="failed", error="numero invalido")
    await update_message_status(LOC, provider_message_id="WA3", status="read")

    s = db()
    linha = s.query(Message).filter_by(provider_message_id="WA3").first()
    assert linha.status == "failed"
    assert linha.error_message == "numero invalido"
    s.close()


# --------------------------------------------------------------------------- #
# Roteamento do message.ack do WAHA
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "ack_name,ack_num,esperado",
    [
        ("READ", 3, "read"),
        ("DEVICE", 2, "delivered"),
        ("SERVER", 1, "sent"),
        ("PLAYED", 4, "read"),
        ("ERROR", -1, "failed"),
        (None, 3, "read"),        # sem ackName, cai no número
        (None, 2, "delivered"),
        ("COISA_NOVA", None, None),  # provedor mudou: ignora em vez de mentir
    ],
)
@pytest.mark.asyncio
async def test_ack_do_waha_vira_status(db, monkeypatch, ack_name, ack_num, esperado):
    from services.message_log import persist_message
    from webhooks import waha_receiver

    await persist_message(
        location_id=LOC, channel="whatsapp", direction="outbound", sender_role="ai",
        contact_ref="5547999", text="oi", provider_message_id="WA-ACK", status="sent",
    )
    corpo = {"id": "WA-ACK"}
    if ack_name is not None:
        corpo["ackName"] = ack_name
    if ack_num is not None:
        corpo["ack"] = ack_num

    await waha_receiver.process_waha_ack(LOC, {"payload": corpo})

    s = db()
    atual = s.query(Message).filter_by(provider_message_id="WA-ACK").first().status
    s.close()
    assert atual == (esperado or "sent"), f"ack {ack_name}/{ack_num} virou {atual}"


@pytest.mark.asyncio
async def test_ack_sem_id_nao_explode(db):
    from webhooks import waha_receiver

    await waha_receiver.process_waha_ack(LOC, {"payload": {}})
    await waha_receiver.process_waha_ack(LOC, {})


# --------------------------------------------------------------------------- #
# Áudio da IA vira mídia reproduzível
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_audio_da_ia_e_guardado_e_fica_tocavel(db, monkeypatch, tmp_path):
    """Sem isto a bolha da IA aparece sem player e o cliente não ouve o agente dele."""
    from data.models import MediaBlob
    from services import media_store
    import services.inbound_pipeline as ip

    monkeypatch.setattr(media_store, "SessionLocal", db)

    ogg = b"OggS-conteudo-de-audio"
    data_url = "data:audio/ogg;base64," + base64.b64encode(ogg).decode()

    arq, mime = await ip._guardar_audio_da_ia(LOC, data_url)
    assert arq and arq.endswith(".ogg"), f"filename inesperado: {arq}"
    assert mime == "audio/ogg"

    s = db()
    blob = s.query(MediaBlob).filter_by(location_id=LOC, filename=arq).first()
    assert blob is not None and blob.data == ogg
    s.close()


@pytest.mark.asyncio
async def test_audio_da_ia_com_url_http_nao_e_guardado(db, monkeypatch):
    """Anexo vindo do CRM é URL de terceiro — não é nosso para guardar."""
    import services.inbound_pipeline as ip

    assert await ip._guardar_audio_da_ia(LOC, "https://cdn.exemplo/audio.mp3") == (None, None)
    assert await ip._guardar_audio_da_ia(LOC, "") == (None, None)
    assert await ip._guardar_audio_da_ia(LOC, "data:audio/ogg;base64,") == (None, None)


def test_url_do_proxy_aponta_para_a_rota_que_existe(monkeypatch):
    from utils.config import settings
    import services.inbound_pipeline as ip

    monkeypatch.setattr(settings, "public_base_url", "https://painel.exemplo.com/", raising=False)
    assert ip._url_do_proxy(LOC, "ia-abc.ogg") == f"https://painel.exemplo.com/media/whatsapp/{LOC}/ia-abc.ogg"

    monkeypatch.setattr(settings, "public_base_url", "", raising=False)
    assert ip._url_do_proxy(LOC, "ia-abc.ogg") is None


# --------------------------------------------------------------------------- #
# Transcrição completa o áudio inbound
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_transcricao_completa_o_texto_do_audio_inbound(db):
    """
    O áudio inbound é logado ANTES da IA rodar (para registrar mesmo com IA
    desligada), então nasce com o rótulo. A transcrição volta depois e completa —
    sem isso o painel mostra bolha de áudio sem texto e não dá para ler a conversa
    passando o olho.
    """
    from services.message_log import persist_message, update_message_text

    await persist_message(
        location_id=LOC, channel="whatsapp", direction="inbound", sender_role="contact",
        contact_ref="5547999", message_type="audio", text="[Áudio recebido]",
        provider_message_id="WA-AUDIO", status="delivered",
    )
    await update_message_text(LOC, "WA-AUDIO", "quero saber o preco do plano anual")

    s = db()
    linha = s.query(Message).filter_by(provider_message_id="WA-AUDIO").first()
    assert linha.text == "quero saber o preco do plano anual"
    assert linha.message_type == "audio", "o tipo tem que continuar audio (a bolha ainda toca)"
    s.close()


@pytest.mark.asyncio
async def test_update_de_texto_ignora_vazio_e_id_desconhecido(db):
    from services.message_log import persist_message, update_message_text

    await persist_message(
        location_id=LOC, channel="whatsapp", direction="inbound", sender_role="contact",
        contact_ref="5547999", message_type="audio", text="[Áudio recebido]",
        provider_message_id="WA-X", status="delivered",
    )
    await update_message_text(LOC, "WA-X", "")
    await update_message_text(LOC, "NAO-EXISTE", "algo")

    s = db()
    assert s.query(Message).filter_by(provider_message_id="WA-X").first().text == "[Áudio recebido]"
    assert s.query(Message).count() == 1
    s.close()
