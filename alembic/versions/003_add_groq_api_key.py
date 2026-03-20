"""Add groq_api_key to ai_agents for audio transcription

Revision ID: 003
Revises: 002
Create Date: 2026-03-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    if not _column_exists("ai_agents", "groq_api_key"):
        op.add_column(
            "ai_agents",
            sa.Column("groq_api_key", sa.String(255), nullable=True),
        )


def downgrade() -> None:
    pass
