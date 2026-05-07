"""
Helpers de versionamento dos prompts dos agentes.

Cada vez que o prompt de um AIAgent é gravado (form, regenerate, optimize, save),
chamamos snapshot_prompt() para guardar o estado anterior em agent_prompt_history.
Isso permite voltar versão e analisar a evolução do prompt no tempo.
"""

from typing import Optional

from data.database import SessionLocal
from data.models import AgentPromptHistory, AIAgent
from utils.logger import logger


# Limite de versões mantidas por (location_id, channel) — evita crescer indefinido.
MAX_VERSIONS_PER_AGENT = 50


def snapshot_prompt(
    location_id: str,
    channel: str,
    prompt: str,
    source: str,
    agent_id: Optional[int] = None,
    form_data_snapshot: Optional[dict] = None,
    note: Optional[str] = None,
) -> Optional[int]:
    """
    Persiste uma versão do prompt no histórico.
    Sync — chamar via asyncio.to_thread() em contexto async se necessário.
    Retorna o id da entry criada, None em erro.
    """
    if not prompt:
        return None

    db = SessionLocal()
    try:
        entry = AgentPromptHistory(
            location_id=location_id,
            channel=channel,
            agent_id=agent_id,
            source=source,
            prompt=prompt,
            form_data_snapshot=form_data_snapshot,
            note=note,
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        new_id = entry.id

        # Cap de versões — apaga as mais antigas se estourar
        total = (
            db.query(AgentPromptHistory)
            .filter(
                AgentPromptHistory.location_id == location_id,
                AgentPromptHistory.channel == channel,
            )
            .count()
        )
        if total > MAX_VERSIONS_PER_AGENT:
            excess = total - MAX_VERSIONS_PER_AGENT
            old_ids = (
                db.query(AgentPromptHistory.id)
                .filter(
                    AgentPromptHistory.location_id == location_id,
                    AgentPromptHistory.channel == channel,
                )
                .order_by(AgentPromptHistory.created_at.asc())
                .limit(excess)
                .all()
            )
            ids_to_drop = [r[0] for r in old_ids]
            if ids_to_drop:
                db.query(AgentPromptHistory).filter(
                    AgentPromptHistory.id.in_(ids_to_drop)
                ).delete(synchronize_session=False)
                db.commit()

        return new_id
    except Exception as e:
        logger.error(f"Falha ao snapshot prompt: {e}")
        db.rollback()
        return None
    finally:
        db.close()


def list_history(
    location_id: str, channel: str = "whatsapp", limit: int = 30
) -> list[dict]:
    """Retorna versões mais recentes primeiro."""
    db = SessionLocal()
    try:
        rows = (
            db.query(AgentPromptHistory)
            .filter(
                AgentPromptHistory.location_id == location_id,
                AgentPromptHistory.channel == channel,
            )
            .order_by(AgentPromptHistory.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id,
                "source": r.source,
                "note": r.note,
                "prompt_preview": (r.prompt or "")[:200],
                "prompt_length": len(r.prompt or ""),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    finally:
        db.close()


def get_version(history_id: int) -> Optional[dict]:
    """Retorna prompt completo de uma versão específica."""
    db = SessionLocal()
    try:
        r = db.query(AgentPromptHistory).filter(AgentPromptHistory.id == history_id).first()
        if not r:
            return None
        return {
            "id": r.id,
            "location_id": r.location_id,
            "channel": r.channel,
            "agent_id": r.agent_id,
            "source": r.source,
            "note": r.note,
            "prompt": r.prompt,
            "form_data_snapshot": r.form_data_snapshot,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
    finally:
        db.close()


def restore_version(history_id: int) -> Optional[dict]:
    """
    Restaura um prompt antigo no agente vivo. Cria uma nova snapshot 'restore'
    referenciando a versão restaurada (rastreabilidade — undo do undo).
    Retorna {success, location_id, channel} ou None em erro.
    """
    version = get_version(history_id)
    if not version:
        return None

    db = SessionLocal()
    try:
        agent = (
            db.query(AIAgent)
            .filter(
                AIAgent.location_id == version["location_id"],
                AIAgent.channel == version["channel"],
            )
            .first()
        )
        if not agent:
            return None

        agent.prompt = version["prompt"]
        db.commit()
    except Exception as e:
        logger.error(f"Erro ao restaurar prompt versão {history_id}: {e}")
        db.rollback()
        return None
    finally:
        db.close()

    snapshot_prompt(
        location_id=version["location_id"],
        channel=version["channel"],
        prompt=version["prompt"],
        source="restore",
        note=f"Restaurado da versão #{history_id}",
    )

    return {
        "success": True,
        "location_id": version["location_id"],
        "channel": version["channel"],
        "restored_from_id": history_id,
    }
