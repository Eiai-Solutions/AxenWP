"""usage_logs: tokens de cache, buscas web e a origem do gasto

Revision ID: 036
Revises: 035
Create Date: 2026-08-19

O painel mostrava **$0,0000** com 2.650 tokens gastos. Causa: `save_usage_log`
recebia `cost_usd` e ninguém passava — nunca houve cálculo. Consertar isso exige
três dados que a tabela não guardava:

1. **`cache_read_tokens` / `cache_write_tokens`** — a resposta da Anthropic traz
   os dois desde sempre e o log descartava. São preços distintos (leitura ~10%
   do input, escrita 125%); com ~87% de reaproveitamento de prefixo, ignorá-los
   erra a conta em ordem de grandeza, para os dois lados.

2. **`buscas_web`** — `web_search` é cobrada por REQUISIÇÃO ($10/1k), fora dos
   tokens. A entrevista da Mestre usa. Sem a coluna, some da conta.

3. **`origem`** — `atendimento` vs `mestre`. Era a pergunta do dono ("o custo do
   agente nos atendimentos E o custo da Mestre") e não havia dimensão para
   responder. `server_default` marca as linhas antigas como `atendimento`, que é
   a verdade: nenhum caminho da Mestre gravava log até hoje.

`cost_usd` continua nullable e passa a significar NULL = "não precificado"
(modelo fora da tabela) em vez de 0.0 = "não gastou". Nada a alterar no schema —
a coluna já aceitava NULL; muda só quem escreve.

**Custo das linhas antigas não é preenchido aqui de propósito.** Fazer isso
exigiria a tabela de preços dentro da migration, e migration que falha derruba o
boot desde a 032. O backfill é `scripts/backfill_custos.py`, rodado à mão.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "036"
down_revision: Union[str, None] = "035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABELA = "usage_logs"

COLUNAS = (
    ("cache_read_tokens", sa.Integer(), "0"),
    ("cache_write_tokens", sa.Integer(), "0"),
    ("buscas_web", sa.Integer(), "0"),
    ("origem", sa.String(20), "atendimento"),
)

IDX_ORIGEM = "ix_usage_logs_origem"


def _tabela_existe(t: str) -> bool:
    return t in sa.inspect(op.get_bind()).get_table_names()


def _coluna_existe(t: str, c: str) -> bool:
    if not _tabela_existe(t):
        return False
    return c in {col["name"] for col in sa.inspect(op.get_bind()).get_columns(t)}


def _indice_existe(t: str, nome: str) -> bool:
    if not _tabela_existe(t):
        return False
    return any(i.get("name") == nome for i in sa.inspect(op.get_bind()).get_indexes(t))


def upgrade() -> None:
    if not _tabela_existe(TABELA):
        # Banco novo: `create_all` no lifespan cria a tabela já completa.
        return

    for nome, tipo, padrao in COLUNAS:
        if not _coluna_existe(TABELA, nome):
            # `server_default` (e não só o default do ORM) porque é ele que
            # preenche as LINHAS QUE JÁ EXISTEM. Sem isso o histórico nasce NULL
            # e toda soma no painel precisaria de COALESCE para não sumir.
            op.add_column(
                TABELA,
                sa.Column(nome, tipo, nullable=True, server_default=padrao),
            )

    if _coluna_existe(TABELA, "origem") and not _indice_existe(TABELA, IDX_ORIGEM):
        op.create_index(IDX_ORIGEM, TABELA, ["origem"])


def downgrade() -> None:
    if _indice_existe(TABELA, IDX_ORIGEM):
        op.drop_index(IDX_ORIGEM, table_name=TABELA)
    for nome, _tipo, _padrao in COLUNAS:
        if _coluna_existe(TABELA, nome):
            op.drop_column(TABELA, nome)
