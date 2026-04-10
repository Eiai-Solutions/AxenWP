"""Add channel column to ai_agents for multi-channel agents per tenant

Revision ID: 011
Revises: 010
Create Date: 2026-04-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column in {c["name"] for c in inspector.get_columns(table)}


def _constraint_exists(table: str, name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    uniques = inspector.get_unique_constraints(table)
    return any(c["name"] == name for c in uniques)


def _index_exists(table: str, name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(i["name"] == name for i in inspector.get_indexes(table))


def upgrade() -> None:
    # 1. Adicionar coluna channel com default 'whatsapp'
    if not _column_exists("ai_agents", "channel"):
        op.add_column(
            "ai_agents",
            sa.Column("channel", sa.String(), nullable=False, server_default="whatsapp"),
        )

    # 2. Remover a unique constraint antiga em location_id (auto-nomeada pelo Postgres)
    # Tenta o nome padrão do Postgres primeiro
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    unique_constraints = inspector.get_unique_constraints("ai_agents")
    for uc in unique_constraints:
        cols = uc.get("column_names", [])
        if cols == ["location_id"]:
            op.drop_constraint(uc["name"], "ai_agents", type_="unique")
            break

    # Também tentar dropar índice unique implícito (se houver)
    indexes = inspector.get_indexes("ai_agents")
    for idx in indexes:
        if idx.get("unique") and idx.get("column_names") == ["location_id"]:
            op.drop_index(idx["name"], table_name="ai_agents")
            break

    # 3. Adicionar nova unique constraint em (location_id, channel)
    if not _constraint_exists("ai_agents", "uq_ai_agent_location_channel"):
        op.create_unique_constraint(
            "uq_ai_agent_location_channel",
            "ai_agents",
            ["location_id", "channel"],
        )


def downgrade() -> None:
    if _constraint_exists("ai_agents", "uq_ai_agent_location_channel"):
        op.drop_constraint("uq_ai_agent_location_channel", "ai_agents", type_="unique")
    if _column_exists("ai_agents", "channel"):
        op.drop_column("ai_agents", "channel")
