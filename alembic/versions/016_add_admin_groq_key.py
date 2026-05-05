"""Add admin_groq_api_key to system_settings (global Groq STT key).

Revision ID: 016
"""

from alembic import op
import sqlalchemy as sa


revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def _column_exists(table, column):
    from sqlalchemy import inspect as sa_inspect
    bind = op.get_bind()
    insp = sa_inspect(bind)
    cols = [c["name"] for c in insp.get_columns(table)]
    return column in cols


def upgrade():
    if not _column_exists("system_settings", "admin_groq_api_key"):
        op.add_column(
            "system_settings",
            sa.Column("admin_groq_api_key", sa.String(512), nullable=True),
        )


def downgrade():
    if _column_exists("system_settings", "admin_groq_api_key"):
        op.drop_column("system_settings", "admin_groq_api_key")
