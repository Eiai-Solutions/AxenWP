"""Registro de uso de APIs externas (OpenRouter, Groq, ElevenLabs) por tenant."""

from typing import Optional

from data.database import SessionLocal
from data.models import UsageLog
from utils.logger import logger


def save_usage_log(
    location_id: str,
    service: str,
    model: Optional[str] = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    characters: int = 0,
    cost_usd: float = 0.0,
) -> None:
    """
    Persiste um registro de uso. Sync por design — chamar via asyncio.to_thread().

    service: openrouter | groq | elevenlabs
    """
    db = SessionLocal()
    try:
        log = UsageLog(
            location_id=location_id,
            service=service,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            characters=characters,
            cost_usd=cost_usd,
        )
        db.add(log)
        db.commit()
    except Exception as e:
        logger.error(f"Erro ao salvar usage log: {e}")
        db.rollback()
    finally:
        db.close()
