"""Add IA Mestre engine choice + Anthropic model to system_settings

Revision ID: 028
Revises: 027
Create Date: 2026-07-22

Aditiva e idempotente. Deixa o operador escolher o motor da IA Mestre
(OpenRouter legado vs Anthropic estruturado) e o modelo Anthropic pelo painel, em
vez de só por env. Default 'openrouter' preserva o comportamento atual.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "028"
down_revision: Union[str, None] = "027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    return column in [col["name"] for col in sa.inspect(bind).get_columns(table)]


def upgrade() -> None:
    if not _column_exists("system_settings", "master_engine"):
        op.add_column(
            "system_settings",
            sa.Column("master_engine", sa.String(20), server_default="openrouter", nullable=False),
        )
    if not _column_exists("system_settings", "admin_anthropic_model"):
        op.add_column("system_settings", sa.Column("admin_anthropic_model", sa.String(80), nullable=True))


def downgrade() -> None:
    for col in ("admin_anthropic_model", "master_engine"):
        if _column_exists("system_settings", col):
            op.drop_column("system_settings", col)
