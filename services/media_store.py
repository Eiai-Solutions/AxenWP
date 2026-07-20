"""
Armazenamento durável da mídia recebida do WhatsApp.

Motivo em uma frase: o WAHA apaga o arquivo em ~180s e o GHL busca o anexo de
entrada de forma preguiçosa (quando o operador abre a conversa), então o binário
precisa viver do nosso lado. Guardamos no Postgres — que já existe e é durável —
no momento do inbound, enquanto o arquivo ainda está no WAHA.

Teto de tamanho: voz/imagem/PDF pequenos cabem folgado; arquivo grande não é
persistido (cairia num bytea gigante) e segue pelo proxy ao vivo, que serve
enquanto o arquivo está fresco no WAHA.
"""

import asyncio
from typing import Optional

from data.database import SessionLocal
from data.models import MediaBlob
from utils.logger import logger

# 25 MB: cobre voz (KB), imagem e a maioria dos PDFs; acima disso não vai pro banco.
MAX_BLOB_BYTES = 25 * 1024 * 1024


def _save_sync(location_id: str, filename: str, content_type: str, data: bytes) -> None:
    db = SessionLocal()
    try:
        row = db.get(MediaBlob, (location_id, filename))
        if row:
            row.content_type = content_type
            row.size = len(data)
            row.data = data
        else:
            db.add(MediaBlob(
                location_id=location_id, filename=filename,
                content_type=content_type, size=len(data), data=data,
            ))
        db.commit()
    finally:
        db.close()


def _get_sync(location_id: str, filename: str) -> Optional[tuple[bytes, str]]:
    db = SessionLocal()
    try:
        row = db.get(MediaBlob, (location_id, filename))
        return (row.data, row.content_type) if row else None
    finally:
        db.close()


async def store_media(location_id: str, filename: str, content_type: str, data: bytes) -> bool:
    """Persiste o binário. Devolve False se estourou o teto (não é erro fatal)."""
    if not data:
        return False
    if len(data) > MAX_BLOB_BYTES:
        logger.info(f"[MEDIA] {filename} de {location_id} tem {len(data)}B > teto; não persistido (proxy ao vivo).")
        return False
    try:
        await asyncio.to_thread(_save_sync, location_id, filename, content_type, data or b"")
        return True
    except Exception as e:
        logger.error(f"[MEDIA] Falha ao persistir {filename} de {location_id}: {e}")
        return False


async def get_media(location_id: str, filename: str) -> Optional[tuple[bytes, str]]:
    """(bytes, content_type) se estiver guardado, senão None."""
    try:
        return await asyncio.to_thread(_get_sync, location_id, filename)
    except Exception as e:
        logger.error(f"[MEDIA] Falha ao ler {filename} de {location_id}: {e}")
        return None


def cleanup_old_media(max_age_days: int = 90) -> None:
    """Remove mídia antiga — o CRM hot-linka, então isso limita quanto tempo o áudio toca."""
    from datetime import datetime, timedelta

    corte = datetime.utcnow() - timedelta(days=max_age_days)
    db = SessionLocal()
    try:
        n = db.query(MediaBlob).filter(MediaBlob.created_at < corte).delete(synchronize_session=False)
        db.commit()
        if n:
            logger.info(f"[MEDIA] Limpeza: {n} mídia(s) com mais de {max_age_days} dias removida(s).")
    except Exception as e:
        db.rollback()
        logger.error(f"[MEDIA] Falha na limpeza de mídia: {e}")
    finally:
        db.close()
