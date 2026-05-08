"""Add tts_provider + Fish Audio TTS columns to ai_agents.

Revision ID: 019
"""

from alembic import op
import sqlalchemy as sa


revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def _column_exists(table, column):
    from sqlalchemy import inspect as sa_inspect
    bind = op.get_bind()
    insp = sa_inspect(bind)
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade():
    # Provider selector — 'elevenlabs' (default) ou 'fishaudio'
    if not _column_exists("ai_agents", "tts_provider"):
        op.add_column(
            "ai_agents",
            sa.Column(
                "tts_provider",
                sa.String(20),
                nullable=False,
                server_default="elevenlabs",
            ),
        )

    if not _column_exists("ai_agents", "fishaudio_api_key"):
        op.add_column(
            "ai_agents", sa.Column("fishaudio_api_key", sa.String(255), nullable=True)
        )
    if not _column_exists("ai_agents", "fishaudio_voice_id"):
        op.add_column(
            "ai_agents", sa.Column("fishaudio_voice_id", sa.String(100), nullable=True)
        )
    if not _column_exists("ai_agents", "fishaudio_model"):
        op.add_column(
            "ai_agents",
            sa.Column(
                "fishaudio_model",
                sa.String(20),
                nullable=False,
                server_default="s1",
            ),
        )
    if not _column_exists("ai_agents", "fishaudio_speed"):
        op.add_column(
            "ai_agents",
            sa.Column("fishaudio_speed", sa.Float(), nullable=True, server_default="1.0"),
        )


def downgrade():
    for col in (
        "fishaudio_speed",
        "fishaudio_model",
        "fishaudio_voice_id",
        "fishaudio_api_key",
        "tts_provider",
    ):
        if _column_exists("ai_agents", col):
            op.drop_column("ai_agents", col)
