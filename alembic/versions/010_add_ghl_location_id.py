"""Add ghl_location_id to tenants for whatsapp_only CRM connection

Revision ID: 010
Revises: 009
Create Date: 2026-04-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    if not _column_exists("tenants", "ghl_location_id"):
        op.add_column("tenants", sa.Column("ghl_location_id", sa.String(), nullable=True))


def downgrade() -> None:
    if _column_exists("tenants", "ghl_location_id"):
        op.drop_column("tenants", "ghl_location_id")
