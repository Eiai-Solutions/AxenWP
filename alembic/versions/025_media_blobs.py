"""Add media_blobs table (mídia do WhatsApp persistida para o CRM tocar)

Revision ID: 025
Revises: 024
Create Date: 2026-07-20

Aditiva e idempotente. O WAHA apaga o arquivo local em ~180s e o GHL hot-linka o
anexo de entrada (busca preguiçosa, sem re-hospedar) — sem persistência própria o
player fica órfão. Guardamos o binário no inbound e o proxy serve daqui.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "025"
down_revision: Union[str, None] = "024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table: str) -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(table)


def upgrade() -> None:
    if _table_exists("media_blobs"):
        return
    op.create_table(
        "media_blobs",
        sa.Column("location_id", sa.String(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("content_type", sa.String(), nullable=False, server_default="application/octet-stream"),
        sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("location_id", "filename"),
    )
    op.create_index("ix_media_blobs_created_at", "media_blobs", ["created_at"])


def downgrade() -> None:
    if _table_exists("media_blobs"):
        op.drop_index("ix_media_blobs_created_at", table_name="media_blobs")
        op.drop_table("media_blobs")
