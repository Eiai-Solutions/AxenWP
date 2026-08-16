"""Add agent_drafts — o wizard salva incremental

Revision ID: 033
Revises: 032
Create Date: 2026-08-16

Aditiva e idempotente. Rascunho vive FORA de `ai_agents` de propósito: agente pela
metade na tabela real seria visível ao runtime na primeira query que esquecesse o
filtro de status. Aqui o runtime nem consulta.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "033"
down_revision: Union[str, None] = "032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(t: str) -> bool:
    return t in sa.inspect(op.get_bind()).get_table_names()


def _index_exists(t: str, i: str) -> bool:
    if not _table_exists(t):
        return False
    return i in [ix["name"] for ix in sa.inspect(op.get_bind()).get_indexes(t)]


def upgrade() -> None:
    if not _table_exists("agent_drafts"):
        op.create_table(
            "agent_drafts",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("location_id", sa.String(), nullable=False),
            sa.Column("criado_por", sa.String(64), nullable=True),
            sa.Column("etapa_atual", sa.String(32), server_default="canal", nullable=False),
            sa.Column("channel", sa.String(20), nullable=True),
            sa.Column("origem", sa.String(20), nullable=True),
            sa.Column("interview_token", sa.String(64), nullable=True),
            sa.Column("submission_id", sa.Integer(), nullable=True),
            sa.Column("agent_name", sa.String(120), nullable=True),
            sa.Column("prompt", sa.Text(), nullable=True),
            sa.Column("spec", sa.JSON(), nullable=True),
            sa.Column("qualificar", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column("qualification_fields", sa.JSON(), nullable=True),
            sa.Column("qualification_pipeline_id", sa.String(), nullable=True),
            sa.Column("qualification_stage_id", sa.String(), nullable=True),
            sa.Column("status", sa.String(20), server_default="rascunho", nullable=False),
            sa.Column("agent_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            # FK explícita: num banco provisionado só por alembic (sem o create_all
            # do boot) a rede de proteção contra location_id órfão não pode sumir.
            sa.ForeignKeyConstraint(
                ["location_id"], ["tenants.location_id"], ondelete="CASCADE"
            ),
        )
    for nome, colunas in (
        ("ix_agent_drafts_location_id", ["location_id"]),
        ("ix_agent_drafts_criado_por", ["criado_por"]),
    ):
        if not _index_exists("agent_drafts", nome):
            op.create_index(nome, "agent_drafts", colunas)


def downgrade() -> None:
    for nome in ("ix_agent_drafts_criado_por", "ix_agent_drafts_location_id"):
        if _index_exists("agent_drafts", nome):
            op.drop_index(nome, table_name="agent_drafts")
    if _table_exists("agent_drafts"):
        op.drop_table("agent_drafts")
