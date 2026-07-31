"""Add admin_users — login por usuário + senha no painel

Revision ID: 029
Revises: 028
Create Date: 2026-07-31

Aditiva e idempotente. Substitui a senha única compartilhada (`ADMIN_PASSWORD`
comparada com `==`) por contas de operador com hash scrypt.

Não semeia usuário aqui: quem cria o primeiro é `bootstrap_admin_user()` no
lifespan, a partir do ADMIN_PASSWORD já configurado. Fazer isso na migration
exigiria hashear dentro do Alembic e deixaria a senha presa à ordem das
migrations — o bootstrap no boot é idempotente e sempre tem o env à mão.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "029"
down_revision: Union[str, None] = "028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table: str) -> bool:
    bind = op.get_bind()
    return table in sa.inspect(bind).get_table_names()


def _index_exists(table: str, index: str) -> bool:
    bind = op.get_bind()
    if not _table_exists(table):
        return False
    return index in [ix["name"] for ix in sa.inspect(bind).get_indexes(table)]


def upgrade() -> None:
    if not _table_exists("admin_users"):
        op.create_table(
            "admin_users",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("username", sa.String(64), nullable=False),
            sa.Column("password_hash", sa.String(255), nullable=False),
            sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("last_login_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("username", name="uq_admin_users_username"),
        )

    if not _index_exists("admin_users", "ix_admin_users_username"):
        op.create_index("ix_admin_users_username", "admin_users", ["username"])


def downgrade() -> None:
    if _index_exists("admin_users", "ix_admin_users_username"):
        op.drop_index("ix_admin_users_username", table_name="admin_users")
    if _table_exists("admin_users"):
        op.drop_table("admin_users")
