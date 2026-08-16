"""Add agent_interviews — entrevista da IA Mestre entre requests

Revision ID: 031
Revises: 030
Create Date: 2026-08-16

Aditiva e idempotente. A entrevista atravessa vários requests (e, no link
público, um navegador anônimo que fecha a aba e volta), então o estado do loop
de tool-use precisa viver fora da memória do processo.

Não substitui `onboarding_submissions`: quando a entrevista conclui, ela CRIA uma
submission — a mesma coisa que o formulário produz. O caminho daí para frente é o
que já existia.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "031"
down_revision: Union[str, None] = "030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


def _index_exists(table: str, index: str) -> bool:
    if not _table_exists(table):
        return False
    return index in [ix["name"] for ix in sa.inspect(op.get_bind()).get_indexes(table)]


def upgrade() -> None:
    if not _table_exists("agent_interviews"):
        op.create_table(
            "agent_interviews",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("token", sa.String(64), nullable=False),
            sa.Column("tenant_location_id", sa.String(), nullable=False),
            sa.Column("estado", sa.Text(), nullable=True),
            sa.Column("status", sa.String(20), server_default="open", nullable=False),
            sa.Column("submission_id", sa.Integer(), nullable=True),
            sa.Column("tokens_saida", sa.Integer(), server_default="0", nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("token", name="uq_agent_interviews_token"),
        )

    for nome, colunas in (
        ("ix_agent_interviews_token", ["token"]),
        ("ix_agent_interviews_tenant", ["tenant_location_id"]),
    ):
        if not _index_exists("agent_interviews", nome):
            op.create_index(nome, "agent_interviews", colunas)


def downgrade() -> None:
    for nome in ("ix_agent_interviews_tenant", "ix_agent_interviews_token"):
        if _index_exists("agent_interviews", nome):
            op.drop_index(nome, table_name="agent_interviews")
    if _table_exists("agent_interviews"):
        op.drop_table("agent_interviews")
