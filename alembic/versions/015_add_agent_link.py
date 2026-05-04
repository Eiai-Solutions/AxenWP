"""Add linked_to_channel column on ai_agents (channel aliases).

Revision ID: 015
"""

from alembic import op
import sqlalchemy as sa


revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def _column_exists(table, column):
    from sqlalchemy import inspect as sa_inspect
    bind = op.get_bind()
    insp = sa_inspect(bind)
    cols = [c["name"] for c in insp.get_columns(table)]
    return column in cols


def upgrade():
    if not _column_exists("ai_agents", "linked_to_channel"):
        op.add_column(
            "ai_agents",
            sa.Column("linked_to_channel", sa.String(), nullable=True),
        )


def downgrade():
    if _column_exists("ai_agents", "linked_to_channel"):
        op.drop_column("ai_agents", "linked_to_channel")
