"""
Persistência do histórico de chat por sessão.

Usa SQLAlchemy direto contra a tabela chat_histories, equivalente ao
Postgres Chat Memory do n8n. Cada sessão é identificada por location_id+phone.
"""

import asyncio
from typing import List

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from data.database import SessionLocal
from data.models import ChatHistory
from utils.logger import logger


def make_session_id(location_id: str, phone: str) -> str:
    """Convenção de naming: '{location_id}_{phone}'."""
    return f"{location_id}_{phone}"


class PostgresChatMessageHistory:
    """Histórico persistente de mensagens (Human/AI) por session_id."""

    def __init__(self, session_id: str, max_history: int = 20):
        self.session_id = session_id
        self.max_history = max_history

    def _fetch_messages_sync(self) -> List[BaseMessage]:
        db = SessionLocal()
        try:
            records = (
                db.query(ChatHistory)
                .filter(ChatHistory.session_id == self.session_id)
                .order_by(ChatHistory.id.desc())
                .limit(self.max_history)
                .all()
            )
            records.reverse()
            msgs: List[BaseMessage] = []
            for r in records:
                if r.message_type == "human":
                    msgs.append(HumanMessage(content=r.content))
                elif r.message_type == "ai":
                    msgs.append(AIMessage(content=r.content))
            return msgs
        finally:
            db.close()

    async def aget_messages(self) -> List[BaseMessage]:
        return await asyncio.to_thread(self._fetch_messages_sync)

    def _add_message_sync(self, type_: str, content: str) -> None:
        db = SessionLocal()
        try:
            # Extrai location_id do session_id (padrão "{location_id}_{phone}")
            location_id = self.session_id.split("_", 1)[0] if "_" in self.session_id else None
            history = ChatHistory(
                session_id=self.session_id,
                location_id=location_id,
                message_type=type_,
                content=content,
            )
            db.add(history)
            db.commit()
        except Exception as e:
            logger.error(f"Erro ao salvar mensagem no histórico: {e}")
            db.rollback()
        finally:
            db.close()

    async def add_user_message(self, message: str) -> None:
        await asyncio.to_thread(self._add_message_sync, "human", message)

    async def add_ai_message(self, message: str) -> None:
        await asyncio.to_thread(self._add_message_sync, "ai", message)
