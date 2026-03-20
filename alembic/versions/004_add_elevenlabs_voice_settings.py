"""Add ElevenLabs voice settings (speed, stability, similarity) to ai_agents

Revision ID: 004
Revises: 003
Create Date: 2026-03-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    for col_name, default_val in [
        ("elevenlabs_speed", "1.0"),
        ("elevenlabs_stability", "0.5"),
        ("elevenlabs_similarity", "0.75"),
    ]:
        if not _column_exists("ai_agents", col_name):
            op.add_column(
                "ai_agents",
                sa.Column(
                    col_name,
                    sa.Float(),
                    nullable=True,
                    server_default=sa.text(default_val),
                ),
            )


def downgrade() -> None:
    pass
