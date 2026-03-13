"""Add missing columns to tenants and ai_agents

Revision ID: 001
Revises:
Create Date: 2026-03-11

Migração idempotente: verifica existência de cada coluna antes de adicionar.
Seguro rodar múltiplas vezes ou em bancos que já tenham parte das colunas.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column in {c["name"] for c in inspector.get_columns(table)}


def _table_exists(table: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table in inspector.get_table_names()


def upgrade() -> None:
    # --- tenants ---
    if not _column_exists("tenants", "is_active"):
        op.add_column("tenants", sa.Column("is_active", sa.Boolean(), nullable=True, server_default=sa.text("true")))

    # --- ai_agents ---
    if _table_exists("ai_agents"):
        if not _column_exists("ai_agents", "elevenlabs_api_key"):
            op.add_column("ai_agents", sa.Column("elevenlabs_api_key", sa.String(255), nullable=True))

        if not _column_exists("ai_agents", "elevenlabs_voice_id"):
            op.add_column("ai_agents", sa.Column("elevenlabs_voice_id", sa.String(100), nullable=True))

        if not _column_exists("ai_agents", "always_reply_with_audio"):
            op.add_column("ai_agents", sa.Column("always_reply_with_audio", sa.Boolean(), nullable=True, server_default=sa.text("false")))

        if not _column_exists("ai_agents", "updated_at"):
            op.add_column("ai_agents", sa.Column("updated_at", sa.DateTime(), nullable=True))

    # --- system_settings (criada pelo create_all, mas garantimos aqui também) ---
    if not _table_exists("system_settings"):
        op.create_table(
            "system_settings",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("admin_openrouter_key", sa.String(512), nullable=True),
            sa.Column("admin_openrouter_model", sa.String(100), nullable=True, server_default=sa.text("'openai/gpt-4o'")),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    # Downgrade intencional não destrói dados — apenas documenta a reversão lógica
    pass
