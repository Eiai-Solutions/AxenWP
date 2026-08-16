"""Agente novo nasce no motor SDK (claude)

Revision ID: 032
Revises: 031
Create Date: 2026-08-16

Só o DEFAULT da coluna. Os 6 agentes que existiam foram migrados à mão em
2026-08-16, depois de o motor ser provado contra os prompts reais deles — não é
papel de migration trocar comportamento de agente que está atendendo lead.

Idempotente: reaplicar não muda nada.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "032"
down_revision: Union[str, None] = "031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    if table not in insp.get_table_names():
        return False
    return column in [c["name"] for c in insp.get_columns(table)]


def upgrade() -> None:
    if not _column_exists("ai_agents", "agent_engine"):
        return
    # SQLite não suporta ALTER de default; lá o default vem do modelo Python.
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column("ai_agents", "agent_engine", server_default="claude")


def downgrade() -> None:
    if _column_exists("ai_agents", "agent_engine") and op.get_bind().dialect.name == "postgresql":
        op.alter_column("ai_agents", "agent_engine", server_default="langchain")
