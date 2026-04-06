"""Add qualification fields to ai_agents and qualified_leads table

Revision ID: 009
Revises: 008
Create Date: 2026-04-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column in {c["name"] for c in inspector.get_columns(table)}


def _table_exists(table: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table in inspector.get_table_names()


def upgrade() -> None:
    # Novos campos em ai_agents
    new_columns = [
        ("qualification_enabled", sa.Boolean(), False),
        ("qualification_pipeline_id", sa.String(), None),
        ("qualification_stage_id", sa.String(), None),
        ("qualification_fields", sa.JSON(), None),
        ("qualification_summary_prompt", sa.Text(), None),
    ]
    for col_name, col_type, default in new_columns:
        if not _column_exists("ai_agents", col_name):
            op.add_column("ai_agents", sa.Column(col_name, col_type, nullable=True, server_default=str(default) if default is not None else None))

    # Tabela qualified_leads
    if not _table_exists("qualified_leads"):
        op.create_table(
            "qualified_leads",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("location_id", sa.String(), sa.ForeignKey("tenants.location_id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("phone", sa.String(), nullable=False, index=True),
            sa.Column("ghl_opportunity_id", sa.String(), nullable=True),
            sa.Column("qualified_data", sa.JSON(), nullable=True),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), default=sa.func.now()),
            sa.UniqueConstraint("location_id", "phone", name="uq_qualified_lead_location_phone"),
        )


def downgrade() -> None:
    if _table_exists("qualified_leads"):
        op.drop_table("qualified_leads")

    for col_name in ["qualification_summary_prompt", "qualification_fields", "qualification_stage_id", "qualification_pipeline_id", "qualification_enabled"]:
        if _column_exists("ai_agents", col_name):
            op.drop_column("ai_agents", col_name)
