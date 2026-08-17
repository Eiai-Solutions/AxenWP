"""Add channel_accounts — a conta de canal passa a existir

Revision ID: 034
Revises: 033
Create Date: 2026-08-17

ADITIVA E SEM MUDANÇA DE COMPORTAMENTO, de propósito. Ao fim desta migration a
aplicação roda idêntica: nada lê `channel_accounts` ainda. A diferença é que passou
a existir onde gravar a segunda conta.

Não ser observável pelo operador é a PROVA de que foi bem feita.

O que ela faz:
  1. cria `channel_accounts`;
  2. adiciona `ai_agents.channel_account_id` NULLABLE;
  3. faz o backfill 1:1 a partir das colunas planas do `Tenant`, e liga o agente
     de cada canal à conta correspondente.

O que ela NÃO faz, e é o ponto:
  - não remove nem para de escrever as colunas do `Tenant`. Durante a transição há
    dual-write, para que um rollback para o release anterior não perca credencial;
  - não toca em `uq_ai_agent_location_channel`. Essa trava é a única coisa que hoje
    impede os `.first()` sem filtro de virarem loteria, e sai só quando todo call
    site endereçar por conta — numa migration própria, depois.

Idempotente pelos helpers, como manda o projeto: ela roda no boot, e migration que
falha DERRUBA o boot.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "034"
down_revision: Union[str, None] = "033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(t: str) -> bool:
    return t in sa.inspect(op.get_bind()).get_table_names()


def _column_exists(t: str, c: str) -> bool:
    if not _table_exists(t):
        return False
    return c in [col["name"] for col in sa.inspect(op.get_bind()).get_columns(t)]


def _index_exists(t: str, i: str) -> bool:
    if not _table_exists(t):
        return False
    return i in [ix["name"] for ix in sa.inspect(op.get_bind()).get_indexes(t)]


def upgrade() -> None:
    if not _table_exists("channel_accounts"):
        op.create_table(
            "channel_accounts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("location_id", sa.String(), nullable=False),
            sa.Column("channel", sa.String(20), nullable=False),
            sa.Column("label", sa.String(120), nullable=True),
            sa.Column("external_ref", sa.String(160), nullable=True),
            sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
            # Z-API
            sa.Column("zapi_instance_id", sa.String(), nullable=True),
            sa.Column("zapi_token", sa.String(), nullable=True),
            sa.Column("zapi_client_token", sa.String(), nullable=True),
            # WAHA
            sa.Column("waha_base_url", sa.String(), nullable=True),
            sa.Column("waha_session", sa.String(), nullable=True),
            sa.Column("waha_engine", sa.String(), nullable=True),
            sa.Column("waha_api_key", sa.String(), nullable=True),
            # Telegram
            sa.Column("telegram_bot_token", sa.String(), nullable=True),
            sa.Column("telegram_bot_username", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["location_id"], ["tenants.location_id"], ondelete="CASCADE"),
            sa.UniqueConstraint("location_id", "channel", "external_ref",
                                name="uq_channel_account_ref"),
        )

    if not _index_exists("channel_accounts", "ix_channel_accounts_location_id"):
        op.create_index("ix_channel_accounts_location_id", "channel_accounts", ["location_id"])
    if not _index_exists("channel_accounts", "ix_channel_accounts_channel"):
        op.create_index("ix_channel_accounts_channel", "channel_accounts", ["channel"])

    if not _column_exists("ai_agents", "channel_account_id"):
        op.add_column("ai_agents", sa.Column("channel_account_id", sa.Integer(), nullable=True))
    if not _index_exists("ai_agents", "ix_ai_agents_channel_account_id"):
        op.create_index("ix_ai_agents_channel_account_id", "ai_agents", ["channel_account_id"])

    _backfill(op.get_bind())


def _backfill(bind) -> None:
    """
    Uma conta por canal configurado, copiada das colunas planas do tenant.

    Roda só onde ainda não há conta daquele canal — reexecutar a migration não
    duplica. O `external_ref` é a chave natural do provedor, que é justamente o que
    o webhook vai usar para dizer QUEM recebeu a mensagem:

        Z-API     → instance_id
        WAHA      → nome da sessão
        Telegram  → @username do bot

    Tenant sem `external_ref` (config pela metade) ainda gera conta, com ref nulo:
    a trava é `UNIQUE(location_id, channel, external_ref)` e vários NULLs convivem
    em Postgres e SQLite. Preferível a pular a conta e deixar o agente órfão.
    """
    tenants = bind.execute(sa.text("""
        SELECT location_id, company_name, whatsapp_provider,
               zapi_instance_id, zapi_token, zapi_client_token,
               waha_base_url, waha_session, waha_engine, waha_api_key,
               telegram_bot_token, telegram_bot_username
        FROM tenants
    """)).mappings().all()

    for t in tenants:
        loc = t["location_id"]
        empresa = (t["company_name"] or loc)[:80]
        provedor = (t["whatsapp_provider"] or "zapi").lower()

        contas = []

        # WhatsApp: uma conta só, do provedor ATIVO da instância. Copiar os dois
        # conjuntos de credencial criaria duas contas para o mesmo número.
        if provedor == "waha" and (t["waha_base_url"] or t["waha_session"]):
            contas.append({
                "channel": "whatsapp",
                "label": f"WhatsApp — {empresa}",
                "external_ref": t["waha_session"],
                "waha_base_url": t["waha_base_url"], "waha_session": t["waha_session"],
                "waha_engine": t["waha_engine"], "waha_api_key": t["waha_api_key"],
            })
        elif provedor != "waha" and t["zapi_instance_id"]:
            contas.append({
                "channel": "whatsapp",
                "label": f"WhatsApp — {empresa}",
                "external_ref": t["zapi_instance_id"],
                "zapi_instance_id": t["zapi_instance_id"], "zapi_token": t["zapi_token"],
                "zapi_client_token": t["zapi_client_token"],
            })

        if t["telegram_bot_token"]:
            contas.append({
                "channel": "telegram",
                "label": f"Telegram — {t['telegram_bot_username'] or empresa}",
                "external_ref": t["telegram_bot_username"],
                "telegram_bot_token": t["telegram_bot_token"],
                "telegram_bot_username": t["telegram_bot_username"],
            })

        for conta in contas:
            ja = bind.execute(
                sa.text("SELECT id FROM channel_accounts "
                        "WHERE location_id = :loc AND channel = :ch"),
                {"loc": loc, "ch": conta["channel"]},
            ).first()
            if ja:
                conta_id = ja[0]
            else:
                # `is_active` explícito: este INSERT é SQL cru e não passa pelo
                # `default=True` do ORM.
                colunas = ["location_id", "is_active"] + list(conta.keys())
                valores = {"location_id": loc, "is_active": True, **conta}
                bind.execute(
                    sa.text(
                        f"INSERT INTO channel_accounts ({', '.join(colunas)}) "
                        f"VALUES ({', '.join(':' + c for c in colunas)})"
                    ),
                    valores,
                )
                conta_id = bind.execute(
                    sa.text("SELECT id FROM channel_accounts "
                            "WHERE location_id = :loc AND channel = :ch"),
                    {"loc": loc, "ch": conta["channel"]},
                ).first()[0]

            # Liga o agente daquele canal. Só onde ainda está nulo: se alguém já
            # ligou à mão, a migration não desfaz.
            bind.execute(
                sa.text("UPDATE ai_agents SET channel_account_id = :cid "
                        "WHERE location_id = :loc AND channel = :ch "
                        "AND channel_account_id IS NULL"),
                {"cid": conta_id, "loc": loc, "ch": conta["channel"]},
            )


def downgrade() -> None:
    # Só o que esta migration criou. As colunas do tenant nunca foram tocadas.
    if _column_exists("ai_agents", "channel_account_id"):
        op.drop_column("ai_agents", "channel_account_id")
    if _table_exists("channel_accounts"):
        op.drop_table("channel_accounts")
