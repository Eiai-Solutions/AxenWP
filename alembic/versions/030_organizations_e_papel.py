"""Organizations + papel do usuario — base do painel do cliente

Revision ID: 030
Revises: 029
Create Date: 2026-07-31

Aditiva e idempotente. Prepara o isolamento por cliente:

- `organizations`: a EMPRESA cliente. Um tenant é um número de WhatsApp, não uma
  empresa — a mesma empresa pode ter comercial e suporte em números distintos, e
  quem paga é ela.
- `tenants.organization_id`: nullable de propósito. Tenant sem dono não é visível
  por cliente nenhum — falha fechada, e os 5 tenants atuais seguem funcionando.
- `admin_users.role`: default 'operator' preserva as contas existentes. Só quando
  existir role='client' é que o escopo passa a valer para alguém.

Não cria organização nem usuário cliente: esta migration só abre espaço. Ligar o
cliente antes da barreira de rota estar de pé daria acesso às 86 rotas de /admin.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "030"
down_revision: Union[str, None] = "029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


def _column_exists(table: str, column: str) -> bool:
    if not _table_exists(table):
        return False
    return column in [c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)]


def _index_exists(table: str, index: str) -> bool:
    if not _table_exists(table):
        return False
    return index in [ix["name"] for ix in sa.inspect(op.get_bind()).get_indexes(table)]


def upgrade() -> None:
    if not _table_exists("organizations"):
        op.create_table(
            "organizations",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("name", sa.String(160), nullable=False),
            sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )

    # FK sem constraint no SQLite (ALTER de constraint não é suportado lá, e o
    # projeto roda SQLite em dev/teste); no Postgres a constraint entra.
    e_postgres = op.get_bind().dialect.name == "postgresql"

    if not _column_exists("tenants", "organization_id"):
        op.add_column("tenants", sa.Column("organization_id", sa.Integer(), nullable=True))
        if e_postgres:
            op.create_foreign_key(
                "fk_tenants_organization", "tenants", "organizations",
                ["organization_id"], ["id"], ondelete="SET NULL",
            )
    if not _index_exists("tenants", "ix_tenants_organization_id"):
        op.create_index("ix_tenants_organization_id", "tenants", ["organization_id"])

    if not _column_exists("admin_users", "role"):
        op.add_column(
            "admin_users",
            sa.Column("role", sa.String(20), server_default="operator", nullable=False),
        )
    if not _column_exists("admin_users", "organization_id"):
        op.add_column("admin_users", sa.Column("organization_id", sa.Integer(), nullable=True))
        if e_postgres:
            op.create_foreign_key(
                "fk_admin_users_organization", "admin_users", "organizations",
                ["organization_id"], ["id"], ondelete="SET NULL",
            )
    if not _index_exists("admin_users", "ix_admin_users_organization_id"):
        op.create_index("ix_admin_users_organization_id", "admin_users", ["organization_id"])


def downgrade() -> None:
    if _index_exists("admin_users", "ix_admin_users_organization_id"):
        op.drop_index("ix_admin_users_organization_id", table_name="admin_users")
    if _column_exists("admin_users", "organization_id"):
        op.drop_column("admin_users", "organization_id")
    if _column_exists("admin_users", "role"):
        op.drop_column("admin_users", "role")
    if _index_exists("tenants", "ix_tenants_organization_id"):
        op.drop_index("ix_tenants_organization_id", table_name="tenants")
    if _column_exists("tenants", "organization_id"):
        op.drop_column("tenants", "organization_id")
    if _table_exists("organizations"):
        op.drop_table("organizations")
