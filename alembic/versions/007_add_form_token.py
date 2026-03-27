"""Add form_token column to tenants table

Revision ID: 007
Revises: 006
Create Date: 2026-03-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [col["name"] for col in inspector.get_columns(table)]
    return column in columns


def upgrade() -> None:
    if not _column_exists("tenants", "form_token"):
        op.add_column("tenants", sa.Column("form_token", sa.String(), nullable=True))
        op.create_index("ix_tenants_form_token", "tenants", ["form_token"], unique=True)


def downgrade() -> None:
    if _column_exists("tenants", "form_token"):
        op.drop_index("ix_tenants_form_token", "tenants")
        op.drop_column("tenants", "form_token")
