"""Add telegram_bot_token column to tenants for Telegram channel.

Revision ID: 014
"""

from alembic import op
import sqlalchemy as sa


revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def _column_exists(table, column):
    from sqlalchemy import inspect as sa_inspect
    bind = op.get_bind()
    insp = sa_inspect(bind)
    cols = [c["name"] for c in insp.get_columns(table)]
    return column in cols


def upgrade():
    if not _column_exists("tenants", "telegram_bot_token"):
        op.add_column("tenants", sa.Column("telegram_bot_token", sa.String(), nullable=True))
    if not _column_exists("tenants", "telegram_bot_username"):
        op.add_column("tenants", sa.Column("telegram_bot_username", sa.String(), nullable=True))


def downgrade():
    if _column_exists("tenants", "telegram_bot_username"):
        op.drop_column("tenants", "telegram_bot_username")
    if _column_exists("tenants", "telegram_bot_token"):
        op.drop_column("tenants", "telegram_bot_token")
