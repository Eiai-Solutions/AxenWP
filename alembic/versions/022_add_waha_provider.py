"""Add WhatsApp provider selector + WAHA config columns to tenants

Revision ID: 022
Revises: 021
Create Date: 2026-07-14

Aditiva e idempotente. Default whatsapp_provider='zapi' => tenants existentes
seguem exatamente como antes (nenhuma mudança de comportamento).

NOTA de coordenação: a Fase 0 (WS2 identidade) também reservou o número 022 no
plano. Como este branch (reestruturação) aterrissa primeiro, 022 é do provedor
WhatsApp; renumerar o WS2 ao integrar. Ver docs/wiki/decisoes/produto-saas-fase0.md
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "022"
down_revision: Union[str, None] = "021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [col["name"] for col in inspector.get_columns(table)]
    return column in columns


def upgrade() -> None:
    if not _column_exists("tenants", "whatsapp_provider"):
        op.add_column(
            "tenants",
            sa.Column("whatsapp_provider", sa.String(), server_default="zapi", nullable=False),
        )
    for col in ("waha_base_url", "waha_session", "waha_engine", "waha_api_key"):
        if not _column_exists("tenants", col):
            op.add_column("tenants", sa.Column(col, sa.String(), nullable=True))


def downgrade() -> None:
    for col in ("waha_api_key", "waha_engine", "waha_session", "waha_base_url", "whatsapp_provider"):
        if _column_exists("tenants", col):
            op.drop_column("tenants", col)
