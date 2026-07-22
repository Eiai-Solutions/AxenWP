"""Add Claude agent-engine columns (motor tool-use opt-in por agente)

Revision ID: 027
Revises: 026
Create Date: 2026-07-22

Aditiva e idempotente. Default agent_engine='langchain' => todos os agentes
existentes seguem exatamente como antes; o motor 'claude' (tool-use + caching) é
opt-in por agente. Ver docs/wiki/decisoes/agente-claude-agent-sdk.md
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "027"
down_revision: Union[str, None] = "026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    return column in [c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)]


def upgrade() -> None:
    if not _column_exists("ai_agents", "agent_engine"):
        op.add_column("ai_agents", sa.Column(
            "agent_engine", sa.String(20), server_default="langchain", nullable=False))
    for col in ("anthropic_model", "anthropic_api_key"):
        if not _column_exists("ai_agents", col):
            op.add_column("ai_agents", sa.Column(col, sa.String(255), nullable=True))
    if not _column_exists("system_settings", "admin_anthropic_key"):
        op.add_column("system_settings", sa.Column("admin_anthropic_key", sa.String(512), nullable=True))


def downgrade() -> None:
    for col in ("anthropic_api_key", "anthropic_model", "agent_engine"):
        if _column_exists("ai_agents", col):
            op.drop_column("ai_agents", col)
    if _column_exists("system_settings", "admin_anthropic_key"):
        op.drop_column("system_settings", "admin_anthropic_key")
