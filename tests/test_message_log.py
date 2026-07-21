"""
Log de mensagens: upsert idempotente + dedup, a base do painel de chat.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from data.models import Base, Message

LOC = "loc1"


@pytest.fixture
def db(monkeypatch, tmp_path):
    from services import message_log

    engine = create_engine(f"sqlite:///{tmp_path}/m.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(message_log, "SessionLocal", Session)
    return Session


def rows(db):
    s = db()
    try:
        return s.query(Message).order_by(Message.id).all()
    finally:
        s.close()


@pytest.mark.asyncio
async def test_grava_inbound_do_contato(db):
    from services.message_log import persist_message

    await persist_message(
        location_id=LOC, channel="whatsapp", provider="waha",
        direction="inbound", sender_role="contact", contact_ref="5547999",
        text="oi", provider_message_id="WA1",
    )
    r = rows(db)
    assert len(r) == 1
    assert r[0].direction == "inbound" and r[0].sender_role == "contact"
    assert r[0].session_id == f"{LOC}_5547999"  # default


@pytest.mark.asyncio
async def test_eco_reentregue_nao_duplica(db):
    from services.message_log import persist_message

    for _ in range(3):
        await persist_message(
            location_id=LOC, channel="whatsapp", provider="waha",
            direction="outbound", sender_role="ai", contact_ref="5547999",
            text="olá", provider_message_id="WA9",
        )
    assert len(rows(db)) == 1


@pytest.mark.asyncio
async def test_operador_crm_pending_depois_sent_uma_linha(db):
    from services.message_log import persist_message

    # GHL dispara pending e depois sent para o MESMO ghl_message_id.
    await persist_message(
        location_id=LOC, channel="whatsapp", direction="outbound",
        sender_role="operator_crm", contact_ref="5547999", text="oi",
        ghl_message_id="GHL1", status="pending",
    )
    await persist_message(
        location_id=LOC, channel="whatsapp", direction="outbound",
        sender_role="operator_crm", contact_ref="5547999", text="oi",
        ghl_message_id="GHL1", provider_message_id="WA5", status="sent",
    )
    r = rows(db)
    assert len(r) == 1
    # o id do provedor foi anexado e o status promovido de pending -> sent
    assert r[0].provider_message_id == "WA5" and r[0].status == "sent"


@pytest.mark.asyncio
async def test_ia_ganha_ghl_id_no_espelho_mesma_linha(db):
    from services.message_log import persist_message

    # 1) IA envia: nasce com provider_message_id
    await persist_message(
        location_id=LOC, channel="whatsapp", provider="waha", direction="outbound",
        sender_role="ai", contact_ref="5547999", text="resposta", provider_message_id="WA7",
    )
    # 2) espelho no CRM: mesma mensagem ganha o ghl_message_id (casado por provider_message_id)
    await persist_message(
        location_id=LOC, channel="whatsapp", provider="waha", direction="outbound",
        sender_role="ai", contact_ref="5547999", text="resposta",
        provider_message_id="WA7", ghl_message_id="GHL7",
    )
    r = rows(db)
    assert len(r) == 1 and r[0].ghl_message_id == "GHL7"


@pytest.mark.asyncio
async def test_status_promove_por_provider_id(db):
    from services.message_log import persist_message, update_message_status

    await persist_message(
        location_id=LOC, channel="whatsapp", direction="outbound", sender_role="ai",
        contact_ref="5547999", text="x", provider_message_id="WA3", status="sent",
    )
    await update_message_status(LOC, provider_message_id="WA3", status="read")
    assert rows(db)[0].status == "read"


@pytest.mark.asyncio
async def test_duas_mensagens_sem_id_nao_colapsam(db):
    from services.message_log import persist_message

    # Telegram/envio sem id: cada uma é uma linha (não podem virar uma só).
    for t in ("um", "dois"):
        await persist_message(
            location_id=LOC, channel="telegram", provider="telegram",
            direction="inbound", sender_role="contact", contact_ref="123", text=t,
        )
    assert len(rows(db)) == 2


@pytest.mark.asyncio
async def test_sem_location_ou_contato_nao_grava(db):
    from services.message_log import persist_message

    await persist_message(location_id="", channel="whatsapp", direction="inbound",
                          sender_role="contact", contact_ref="x", text="a")
    await persist_message(location_id=LOC, channel="whatsapp", direction="inbound",
                          sender_role="contact", contact_ref="", text="b")
    assert len(rows(db)) == 0


def test_message_type_from_mimetype():
    from services.message_log import message_type_from_mimetype as f
    assert f("audio/ogg; codecs=opus") == "audio"
    assert f("image/webp") == "sticker"
    assert f("image/jpeg") == "image"
    assert f("video/mp4") == "video"
    assert f("application/pdf") == "document"
    assert f(None) == "text"


def test_message_type_from_url():
    from services.message_log import message_type_from_url as f
    # o bug: imagem da Z-API (URL, sem mimetype) não pode virar 'document'
    assert f("https://cdn.z-api.io/abc/foto.jpg") == "image"
    assert f("https://cdn/x.png?tok=1") == "image"
    assert f("https://cdn/video.mp4") == "video"
    assert f("https://cdn/doc.pdf") == "document"
    assert f("https://cdn/qualquer.ogg", is_audio=True) == "audio"
    assert f(None) == "text"
