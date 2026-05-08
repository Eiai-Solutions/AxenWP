"""Add Fish Audio quality controls (temperature, top_p, normalize_loudness).

Revision ID: 020
"""

from alembic import op
import sqlalchemy as sa


revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None


def _column_exists(table, column):
    from sqlalchemy import inspect as sa_inspect
    bind = op.get_bind()
    insp = sa_inspect(bind)
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade():
    if not _column_exists("ai_agents", "fishaudio_temperature"):
        op.add_column(
            "ai_agents",
            sa.Column("fishaudio_temperature", sa.Float(), nullable=True, server_default="0.7"),
        )
    if not _column_exists("ai_agents", "fishaudio_top_p"):
        op.add_column(
            "ai_agents",
            sa.Column("fishaudio_top_p", sa.Float(), nullable=True, server_default="0.7"),
        )
    if not _column_exists("ai_agents", "fishaudio_normalize_loudness"):
        op.add_column(
            "ai_agents",
            sa.Column(
                "fishaudio_normalize_loudness",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            ),
        )


def downgrade():
    for col in (
        "fishaudio_normalize_loudness",
        "fishaudio_top_p",
        "fishaudio_temperature",
    ):
        if _column_exists("ai_agents", col):
            op.drop_column("ai_agents", col)
