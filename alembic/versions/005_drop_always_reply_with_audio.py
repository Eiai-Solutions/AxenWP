"""Drop unused always_reply_with_audio column from ai_agents

Revision ID: 005
Revises: 004
Create Date: 2026-03-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    if _column_exists("ai_agents", "always_reply_with_audio"):
        op.drop_column("ai_agents", "always_reply_with_audio")


def downgrade() -> None:
    if not _column_exists("ai_agents", "always_reply_with_audio"):
        op.add_column(
            "ai_agents",
            sa.Column(
                "always_reply_with_audio",
                sa.Boolean(),
                nullable=True,
                server_default=sa.text("false"),
            ),
        )
