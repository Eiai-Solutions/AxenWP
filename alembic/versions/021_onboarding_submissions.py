"""Add onboarding_submissions table for raw public-form data.

Revision ID: 021
"""

from alembic import op
import sqlalchemy as sa


revision = "021"
down_revision = "020"
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
    if not _table_exists("onboarding_submissions"):
        op.create_table(
            "onboarding_submissions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("tenant_location_id", sa.String(), nullable=False),
            sa.Column("form_data", sa.JSON(), nullable=False),
            sa.Column(
                "status",
                sa.String(20),
                nullable=False,
                server_default="pending",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("processed_at", sa.DateTime(), nullable=True),
        )

    if not _index_exists("onboarding_submissions", "ix_onboarding_tenant_status"):
        op.create_index(
            "ix_onboarding_tenant_status",
            "onboarding_submissions",
            ["tenant_location_id", "status"],
        )


def downgrade():
    if _index_exists("onboarding_submissions", "ix_onboarding_tenant_status"):
        op.drop_index("ix_onboarding_tenant_status", table_name="onboarding_submissions")
    if _table_exists("onboarding_submissions"):
        op.drop_table("onboarding_submissions")
