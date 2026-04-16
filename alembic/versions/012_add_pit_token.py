"""Add pit_token column to tenants for Private Integration Token support.

Revision ID: 012
"""

from alembic import op
import sqlalchemy as sa


revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def _column_exists(table, column):
    from sqlalchemy import inspect as sa_inspect
    bind = op.get_bind()
    insp = sa_inspect(bind)
    cols = [c["name"] for c in insp.get_columns(table)]
    return column in cols


def upgrade():
    if not _column_exists("tenants", "pit_token"):
        op.add_column("tenants", sa.Column("pit_token", sa.String(), nullable=True))


def downgrade():
    if _column_exists("tenants", "pit_token"):
        op.drop_column("tenants", "pit_token")
