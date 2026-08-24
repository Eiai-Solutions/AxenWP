"""Chave de API por tenant — como um CRM de terceiro fala com o hub

Revision ID: 038
Revises: 037
Create Date: 2026-08-20

Até aqui TODA rota administrativa exigia o cookie `admin_session` — autenticação
de humano com navegador. Não havia API key, bearer de tenant nem HMAC de entrada
em lugar nenhum do projeto, então "o controle da IA é do CRM" não tinha por onde
entrar.

Só cria tabela; não mexe em dado nenhum e não muda comportamento. Nenhuma chave
existe até o operador criar a primeira pelo painel, e sem chave a API v1 responde
401 — que é o comportamento certo para uma superfície que ainda não foi liberada.

Guarda o SHA-256 e não a chave. `prefixo` fica em claro só para a tela conseguir
dizer qual é qual; ele não abre nada sozinho.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "038"
down_revision: Union[str, None] = "037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABELA = "tenant_api_keys"


def _tabela_existe(t: str) -> bool:
    return t in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _tabela_existe(TABELA):
        return

    op.create_table(
        TABELA,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("location_id", sa.String(),
                  sa.ForeignKey("tenants.location_id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("nome", sa.String(80), nullable=False),
        sa.Column("prefixo", sa.String(32), nullable=False),
        # UNIQUE no hash: duas chaves com o mesmo hash seriam a mesma chave, e o
        # índice é o que faz a autenticação ser um lookup em vez de uma varredura.
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("criado_por", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    if _tabela_existe(TABELA):
        op.drop_table(TABELA)
