"""Add location_id column + composite indexes to chat_histories.

Revision ID: 017
"""

from alembic import op
import sqlalchemy as sa


revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def _column_exists(table, column):
    from sqlalchemy import inspect as sa_inspect
    bind = op.get_bind()
    insp = sa_inspect(bind)
    cols = [c["name"] for c in insp.get_columns(table)]
    return column in cols


def _index_exists(table, index_name):
    from sqlalchemy import inspect as sa_inspect
    bind = op.get_bind()
    insp = sa_inspect(bind)
    return any(ix["name"] == index_name for ix in insp.get_indexes(table))


def upgrade():
    if not _column_exists("chat_histories", "location_id"):
        op.add_column(
            "chat_histories",
            sa.Column("location_id", sa.String(), nullable=True),
        )

    # Backfill: extrai a parte antes do primeiro "_" no session_id.
    # session_id tem padrão "{location_id}_{phone}", então split_part(session_id, '_', 1).
    # Em SQLite isso falharia, mas o ambiente de produção é Postgres (database_url).
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "UPDATE chat_histories "
            "SET location_id = split_part(session_id, '_', 1) "
            "WHERE location_id IS NULL AND session_id IS NOT NULL"
        )
    else:
        # SQLite (dev/tests): pega tudo antes do primeiro _ via substr/instr
        op.execute(
            "UPDATE chat_histories "
            "SET location_id = substr(session_id, 1, instr(session_id, '_') - 1) "
            "WHERE location_id IS NULL AND session_id IS NOT NULL "
            "AND instr(session_id, '_') > 0"
        )

    if not _index_exists("chat_histories", "ix_chat_histories_location_id"):
        op.create_index(
            "ix_chat_histories_location_id",
            "chat_histories",
            ["location_id"],
        )

    if not _index_exists("chat_histories", "ix_chat_histories_loc_created"):
        op.create_index(
            "ix_chat_histories_loc_created",
            "chat_histories",
            ["location_id", "created_at"],
        )

    if not _index_exists("chat_histories", "ix_chat_histories_created_at"):
        op.create_index(
            "ix_chat_histories_created_at",
            "chat_histories",
            ["created_at"],
        )


def downgrade():
    if _index_exists("chat_histories", "ix_chat_histories_created_at"):
        op.drop_index("ix_chat_histories_created_at", table_name="chat_histories")
    if _index_exists("chat_histories", "ix_chat_histories_loc_created"):
        op.drop_index("ix_chat_histories_loc_created", table_name="chat_histories")
    if _index_exists("chat_histories", "ix_chat_histories_location_id"):
        op.drop_index("ix_chat_histories_location_id", table_name="chat_histories")
    if _column_exists("chat_histories", "location_id"):
        op.drop_column("chat_histories", "location_id")
