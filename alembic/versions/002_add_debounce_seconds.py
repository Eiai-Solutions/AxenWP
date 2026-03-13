"""Add debounce_seconds to ai_agents

Revision ID: 002
Revises: 001
Create Date: 2026-03-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    if not _column_exists("ai_agents", "debounce_seconds"):
        op.add_column(
            "ai_agents",
            sa.Column(
                "debounce_seconds",
                sa.Float(),
                nullable=True,
                server_default=sa.text("1.5"),
            ),
        )


def downgrade() -> None:
    pass
