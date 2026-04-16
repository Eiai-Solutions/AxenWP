"""Add form_data JSON column to ai_agents for storing onboarding form answers.

Revision ID: 013
"""

from alembic import op
import sqlalchemy as sa


revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def _column_exists(table, column):
    from sqlalchemy import inspect as sa_inspect
    bind = op.get_bind()
    insp = sa_inspect(bind)
    cols = [c["name"] for c in insp.get_columns(table)]
    return column in cols


def upgrade():
    if not _column_exists("ai_agents", "form_data"):
        op.add_column("ai_agents", sa.Column("form_data", sa.JSON(), nullable=True))


def downgrade():
    if _column_exists("ai_agents", "form_data"):
        op.drop_column("ai_agents", "form_data")
