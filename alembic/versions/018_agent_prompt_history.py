"""Add agent_prompt_history table for versioning agent prompts.

Revision ID: 018
"""

from alembic import op
import sqlalchemy as sa


revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def _table_exists(table):
    from sqlalchemy import inspect as sa_inspect
    bind = op.get_bind()
    insp = sa_inspect(bind)
    return table in insp.get_table_names()


def _index_exists(table, index_name):
    from sqlalchemy import inspect as sa_inspect
    bind = op.get_bind()
    insp = sa_inspect(bind)
    if not _table_exists(table):
        return False
    return any(ix["name"] == index_name for ix in insp.get_indexes(table))


def upgrade():
    if not _table_exists("agent_prompt_history"):
        op.create_table(
            "agent_prompt_history",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("location_id", sa.String(), nullable=False),
            sa.Column("channel", sa.String(), nullable=False, server_default="whatsapp"),
            sa.Column("agent_id", sa.Integer(), nullable=True),
            # source: 'form' | 'regenerate' | 'optimize_apply' | 'manual_save' | 'restore'
            sa.Column("source", sa.String(50), nullable=False),
            sa.Column("prompt", sa.Text(), nullable=False),
            sa.Column("form_data_snapshot", sa.JSON(), nullable=True),
            sa.Column("note", sa.String(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )

    if not _index_exists("agent_prompt_history", "ix_aph_loc_channel_created"):
        op.create_index(
            "ix_aph_loc_channel_created",
            "agent_prompt_history",
            ["location_id", "channel", "created_at"],
        )


def downgrade():
    if _index_exists("agent_prompt_history", "ix_aph_loc_channel_created"):
        op.drop_index("ix_aph_loc_channel_created", table_name="agent_prompt_history")
    if _table_exists("agent_prompt_history"):
        op.drop_table("agent_prompt_history")
