"""Add mode column to tenants table

Revision ID: 006
Revises: 005
Create Date: 2026-03-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [col["name"] for col in inspector.get_columns(table)]
    return column in columns


def upgrade() -> None:
    if not _column_exists("tenants", "mode"):
        op.add_column("tenants", sa.Column("mode", sa.String(), server_default="ghl"))


def downgrade() -> None:
    if _column_exists("tenants", "mode"):
        op.drop_column("tenants", "mode")
