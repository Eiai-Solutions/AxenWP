"""
Log de mensagens — choke point único de persistência.

Toda mensagem (entrada do contato, chunk de resposta da IA, envio do operador
pelo CRM/painel) passa por `persist_message`. É best-effort: falha aqui NUNCA
derruba o fluxo — a conversa e a resposta importam mais que o log.

Dedup por upsert: procura linha existente por `provider_message_id` e, senão, por
`ghl_message_id` (escopados por location). Achou → atualiza (anexa o id que
faltava, status); não achou → insere. Isso cobre:
- o WAHA reentregando o próprio eco (já barrado antes, mas o upsert é a rede);
- o GHL disparando pending+sent+delivered para o mesmo ghl_message_id;
- a mensagem que nasce com o id do provedor e ganha o id do CRM depois (espelho).
"""

import asyncio
from typing import Optional

from sqlalchemy.exc import IntegrityError

from data.database import SessionLocal
from data.models import Message
from utils.logger import logger


def _norm(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    v = str(v).strip()
    return v or None


def _achar_existente(db, loc: str, pmid: Optional[str], gmid: Optional[str]):
    row = None
    if pmid:
        row = db.query(Message).filter_by(location_id=loc, provider_message_id=pmid).first()
    if row is None and gmid:
        row = db.query(Message).filter_by(location_id=loc, ghl_message_id=gmid).first()
    return row


def _completar(row, pmid, gmid, campos, db) -> None:
    # Não reinsere: só completa o id que faltava e promove o status.
    if pmid and not row.provider_message_id:
        row.provider_message_id = pmid
    if gmid and not row.ghl_message_id:
        row.ghl_message_id = gmid
    novo_status = campos.get("status")
    if novo_status and novo_status != "pending":
        row.status = novo_status
    if campos.get("error_message"):
        row.error_message = campos["error_message"]
    db.commit()


def _persist_sync(campos: dict) -> None:
    db = SessionLocal()
    try:
        pmid = campos.get("provider_message_id")
        gmid = campos.get("ghl_message_id")
        loc = campos["location_id"]

        existente = _achar_existente(db, loc, pmid, gmid)
        if existente is not None:
            _completar(existente, pmid, gmid, campos, db)
            return

        try:
            db.add(Message(**campos))
            db.commit()
        except IntegrityError:
            # Corrida: outro webhook concorrente inseriu a mesma mensagem entre o
            # nosso SELECT e o INSERT. O índice único parcial barrou a duplicata;
            # aqui reconciliamos completando a linha que venceu a corrida.
            db.rollback()
            existente = _achar_existente(db, loc, pmid, gmid)
            if existente is not None:
                _completar(existente, pmid, gmid, campos, db)
    finally:
        db.close()


async def persist_message(
    *,
    location_id: str,
    channel: str,
    direction: str,
    sender_role: str,
    contact_ref: str,
    provider: Optional[str] = None,
    ghl_contact_id: Optional[str] = None,
    sender_name: Optional[str] = None,
    message_type: str = "text",
    text: Optional[str] = None,
    media_filename: Optional[str] = None,
    media_mimetype: Optional[str] = None,
    media_url: Optional[str] = None,
    provider_message_id: Optional[str] = None,
    ghl_message_id: Optional[str] = None,
    status: str = "sent",
    error_message: Optional[str] = None,
    session_id: Optional[str] = None,
) -> None:
    """Grava (ou completa) uma mensagem no log. Best-effort."""
    if not (location_id and contact_ref):
        return
    campos = {
        "location_id": location_id,
        "session_id": session_id or f"{location_id}_{contact_ref}",
        "channel": channel,
        "provider": _norm(provider),
        "contact_ref": contact_ref,
        "ghl_contact_id": _norm(ghl_contact_id),
        "direction": direction,
        "sender_role": sender_role,
        "sender_name": _norm(sender_name),
        "message_type": message_type or "text",
        "text": text,
        "media_filename": _norm(media_filename),
        "media_mimetype": _norm(media_mimetype),
        "media_url": _norm(media_url),
        "provider_message_id": _norm(provider_message_id),
        "ghl_message_id": _norm(ghl_message_id),
        "status": status or "sent",
        "error_message": error_message,
    }
    try:
        await asyncio.to_thread(_persist_sync, campos)
    except Exception as e:
        logger.error(f"[MSGLOG] Falha ao registrar mensagem de {location_id}: {e}")


def _update_status_sync(location_id: str, provider_message_id: Optional[str],
                        ghl_message_id: Optional[str], status: str, error: Optional[str]) -> None:
    db = SessionLocal()
    try:
        q = db.query(Message).filter_by(location_id=location_id)
        if provider_message_id:
            row = q.filter_by(provider_message_id=provider_message_id).first()
        elif ghl_message_id:
            row = q.filter_by(ghl_message_id=ghl_message_id).first()
        else:
            return
        if row:
            row.status = status
            if error:
                row.error_message = error
            db.commit()
    finally:
        db.close()


async def update_message_status(
    location_id: str,
    *,
    provider_message_id: Optional[str] = None,
    ghl_message_id: Optional[str] = None,
    status: str,
    error: Optional[str] = None,
) -> None:
    """Promove o status de entrega de uma mensagem já registrada. Best-effort."""
    try:
        await asyncio.to_thread(
            _update_status_sync, location_id, _norm(provider_message_id),
            _norm(ghl_message_id), status, error,
        )
    except Exception as e:
        logger.error(f"[MSGLOG] Falha ao atualizar status em {location_id}: {e}")


def message_type_from_mimetype(mimetype: Optional[str]) -> str:
    """Deriva o tipo de mensagem para o painel escolher o componente de render."""
    mt = (mimetype or "").lower()
    if mt.startswith("audio/"):
        return "audio"
    if "webp" in mt:
        return "sticker"
    if mt.startswith("image/"):
        return "image"
    if mt.startswith("video/"):
        return "video"
    if mt:
        return "document"
    return "text"


def message_type_from_url(url: Optional[str], is_audio: bool = False) -> str:
    """
    Tipo de mídia a partir da extensão da URL — usado onde não há mimetype (Z-API
    serve por CDN e só dá a URL). Sem isto, imagem/vídeo caíam como 'document'.
    """
    if is_audio:
        return "audio"
    if not url:
        return "text"
    import mimetypes

    mt, _ = mimetypes.guess_type(url.split("?")[0])
    return message_type_from_mimetype(mt)
