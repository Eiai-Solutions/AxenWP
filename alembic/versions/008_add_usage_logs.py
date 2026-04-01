"""Add usage_logs table for cost tracking per tenant

Revision ID: 008
Revises: 007
Create Date: 2026-04-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table in inspector.get_table_names()


def upgrade() -> None:
    if not _table_exists("usage_logs"):
        op.create_table(
            "usage_logs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("location_id", sa.String(), sa.ForeignKey("tenants.location_id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("service", sa.String(50), nullable=False, index=True),
            sa.Column("model", sa.String(100), nullable=True),
            sa.Column("input_tokens", sa.Integer(), default=0),
            sa.Column("output_tokens", sa.Integer(), default=0),
            sa.Column("characters", sa.Integer(), default=0),
            sa.Column("cost_usd", sa.Float(), default=0.0),
            sa.Column("created_at", sa.DateTime(), default=sa.func.now(), index=True),
        )


def downgrade() -> None:
    if _table_exists("usage_logs"):
        op.drop_table("usage_logs")
