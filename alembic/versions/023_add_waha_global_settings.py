"""Add global WAHA server config to system_settings

Revision ID: 023
Revises: 022
Create Date: 2026-07-14

Aditiva e idempotente. Config global do servidor WAHA compartilhado (url + api key),
usada pelo painel para gerenciar sessões (uma sessão por tenant).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "023"
down_revision: Union[str, None] = "022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column in [col["name"] for col in inspector.get_columns(table)]


def upgrade() -> None:
    for col in ("admin_waha_url", "admin_waha_api_key"):
        if not _column_exists("system_settings", col):
            op.add_column("system_settings", sa.Column(col, sa.String(512), nullable=True))


def downgrade() -> None:
    for col in ("admin_waha_api_key", "admin_waha_url"):
        if _column_exists("system_settings", col):
            op.drop_column("system_settings", col)
