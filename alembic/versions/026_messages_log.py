"""Add messages table (log completo de mensagens — base do painel de chat)

Revision ID: 026
Revises: 025
Create Date: 2026-07-20

Aditiva e idempotente. Distinta de chat_histories (memória da IA): append-only,
guarda toda mensagem (contato, IA, operador) com direção, autor, mídia e status.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "026"
down_revision: Union[str, None] = "025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table)


def _index_exists(table: str, index: str) -> bool:
    if not _table_exists(table):
        return False
    return index in [ix["name"] for ix in sa.inspect(op.get_bind()).get_indexes(table)]


def upgrade() -> None:
    if not _table_exists("messages"):
        op.create_table(
            "messages",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("location_id", sa.String(), nullable=False),
            sa.Column("session_id", sa.String(), nullable=False),
            sa.Column("channel", sa.String(), nullable=False),
            sa.Column("provider", sa.String(), nullable=True),
            sa.Column("contact_ref", sa.String(), nullable=False),
            sa.Column("ghl_contact_id", sa.String(), nullable=True),
            sa.Column("direction", sa.String(), nullable=False),
            sa.Column("sender_role", sa.String(), nullable=False),
            sa.Column("sender_name", sa.String(), nullable=True),
            sa.Column("message_type", sa.String(), nullable=False, server_default="text"),
            sa.Column("text", sa.Text(), nullable=True),
            sa.Column("media_filename", sa.String(), nullable=True),
            sa.Column("media_mimetype", sa.String(), nullable=True),
            sa.Column("media_url", sa.String(), nullable=True),
            sa.Column("provider_message_id", sa.String(), nullable=True),
            sa.Column("ghl_message_id", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=False, server_default="sent"),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    # Índices (idempotentes) — busca por thread, por contato, e as chaves de dedup.
    for name, cols in (
        ("ix_messages_location_id", ["location_id"]),
        ("ix_messages_session_id", ["session_id"]),
        ("ix_messages_contact_ref", ["contact_ref"]),
        ("ix_messages_ghl_contact_id", ["ghl_contact_id"]),
        ("ix_messages_provider_message_id", ["provider_message_id"]),
        ("ix_messages_ghl_message_id", ["ghl_message_id"]),
        ("ix_messages_created_at", ["created_at"]),
        ("ix_messages_thread", ["location_id", "session_id", "id"]),
        ("ix_messages_contact", ["location_id", "contact_ref"]),
    ):
        if not _index_exists("messages", name):
            op.create_index(name, "messages", cols)

    # Índices ÚNICOS PARCIAIS = dedup atômico no banco (o upsert do helper sozinho
    # tem janela de corrida entre SELECT e INSERT sob webhooks concorrentes).
    # Parciais porque provider_message_id/ghl_message_id são NULL em vários casos
    # (telegram, envio sem id) e NULLs não podem colidir entre si.
    for name, col in (
        ("uq_messages_provider_mid", "provider_message_id"),
        ("uq_messages_ghl_mid", "ghl_message_id"),
    ):
        if not _index_exists("messages", name):
            op.create_index(
                name, "messages", ["location_id", col], unique=True,
                postgresql_where=sa.text(f"{col} IS NOT NULL"),
                sqlite_where=sa.text(f"{col} IS NOT NULL"),
            )


def downgrade() -> None:
    if _table_exists("messages"):
        op.drop_table("messages")
