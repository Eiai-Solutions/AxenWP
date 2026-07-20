"""Vincula @lid e telefone na mesma linha de contact_mappings

Revision ID: 024
Revises: 023
Create Date: 2026-07-20

A mesma pessoa chega ora como telefone, ora como @lid — o WhatsApp (motor GOWS)
nem sempre entrega o número. Sem guardar as duas identidades juntas, quem entrou
pelo telefone vira um contato NOVO ao aparecer como @lid, e vice-versa.

Aditiva e idempotente: coluna nullable, tenants existentes seguem iguais.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "024"
down_revision: Union[str, None] = "023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column in [col["name"] for col in inspector.get_columns(table)]


def _index_exists(table: str, index: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return index in [ix["name"] for ix in inspector.get_indexes(table)]


def upgrade() -> None:
    if not _column_exists("contact_mappings", "lid"):
        op.add_column("contact_mappings", sa.Column("lid", sa.String(), nullable=True))
    if not _index_exists("contact_mappings", "ix_contact_mappings_lid"):
        op.create_index("ix_contact_mappings_lid", "contact_mappings", ["lid"])


def downgrade() -> None:
    if _index_exists("contact_mappings", "ix_contact_mappings_lid"):
        op.drop_index("ix_contact_mappings_lid", table_name="contact_mappings")
    if _column_exists("contact_mappings", "lid"):
        op.drop_column("contact_mappings", "lid")
