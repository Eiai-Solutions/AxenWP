"""Conserta o que a 034 deixou passar: a FK e as datas

Revision ID: 035
Revises: 034
Create Date: 2026-08-17

Revisão adversarial da 034 achou dois defeitos, os dois confirmados contra o banco
de produção. Esta migration existe porque a 034 JÁ RODOU lá — corrigir o arquivo
dela só conserta ambiente que ainda não migrou.

1. **A FK nunca entrou em produção.** O modelo declara
   `ForeignKey("channel_accounts.id")`, mas a 034 adicionou a coluna como INTEGER
   puro. Como `create_all` não altera tabela que já existe, em produção — onde
   `ai_agents` existe desde sempre — a única via era a migration, e a constraint
   ficou de fora. Num banco nascido do zero (dev, teste) o `create_all` cria a
   tabela inteira já com a FK. **Mesmo código, dois schemas.** Medido:

       producao:  ai_agents -> ['location_id'] -> tenants     (e só)
       dev novo:  ai_agents -> ['location_id'], ['channel_account_id']

   Na fase 3, quando o roteamento passar a endereçar por conta, um ponteiro
   pendurado não seria detectável por nada — nem pelo banco, nem pelo ORM.

   O precedente correto é da `030`, que faz `create_foreign_key` gated por dialeto.
   Mas lá a criação está DENTRO do `if not _column_exists`: com a coluna já
   existindo, ela nunca rodaria. Aqui a FK é checada por conta própria.

2. **`created_at`/`updated_at` nasceram NULL.** Mesmo furo do `is_active`: o
   backfill insere por SQL cru e não passa pelos defaults do ORM. A data de
   criação não dá para reconstruir depois — ou se grava agora, ou some.

`ondelete="SET NULL"`: apagar uma conta não pode apagar o agente junto (o prompt
dele é o ativo mais caro do tenant) nem travar o delete. O agente fica sem conta e
volta a ser resolvido por canal, que é o comportamento de hoje.
"""
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "035"
down_revision: Union[str, None] = "034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FK = "fk_ai_agents_channel_account"


def _table_exists(t: str) -> bool:
    return t in sa.inspect(op.get_bind()).get_table_names()


def _fk_exists(tabela: str, nome: str) -> bool:
    if not _table_exists(tabela):
        return False
    insp = sa.inspect(op.get_bind())
    return any(
        fk.get("name") == nome or "channel_account_id" in (fk.get("constrained_columns") or [])
        for fk in insp.get_foreign_keys(tabela)
    )


def upgrade() -> None:
    bind = op.get_bind()
    e_postgres = bind.dialect.name == "postgresql"

    # Datas primeiro: se a FK falhar por dado inconsistente, as datas já entraram.
    if _table_exists("channel_accounts"):
        bind.execute(
            sa.text("UPDATE channel_accounts SET created_at = :agora WHERE created_at IS NULL"),
            {"agora": datetime.now(timezone.utc)},
        )
        bind.execute(sa.text(
            "UPDATE channel_accounts SET updated_at = created_at "
            "WHERE updated_at IS NULL"
        ))

    # A FK só no Postgres: o SQLite não suporta ALTER de constraint, e lá a
    # checagem é desligada por padrão de qualquer forma — a constraint seria
    # decorativa. Em dev o schema vem do `create_all`, que já a cria.
    if e_postgres and _table_exists("ai_agents") and not _fk_exists("ai_agents", FK):
        # Limpa ponteiro pendurado antes, senão a constraint não entra. Hoje não
        # deveria existir nenhum — mas "não deveria" não é o mesmo que "não há", e
        # migration que falha derruba o boot.
        orfaos = bind.execute(sa.text(
            "UPDATE ai_agents SET channel_account_id = NULL "
            "WHERE channel_account_id IS NOT NULL AND channel_account_id NOT IN "
            "(SELECT id FROM channel_accounts)"
        )).rowcount
        if orfaos:
            print(f"[035] {orfaos} agente(s) apontavam para conta inexistente; desligados.")

        op.create_foreign_key(
            FK, "ai_agents", "channel_accounts",
            ["channel_account_id"], ["id"], ondelete="SET NULL",
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql" and _fk_exists("ai_agents", FK):
        op.drop_constraint(FK, "ai_agents", type_="foreignkey")
