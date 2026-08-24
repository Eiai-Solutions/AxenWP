"""O interruptor da IA vira estado próprio, por conversa e por canal

Revision ID: 037
Revises: 036
Create Date: 2026-08-20

Até aqui, "IA pausada" não era um estado — era efeito colateral de uma linha em
`qualified_leads`. Para pausar quando o lead pedia um humano, o
`escalation_handler` gravava um LEAD QUALIFICADO FALSO (`_handoff: True`), e
religar a IA exigia APAGAR o registro de qualificação.

Esta migration cria `conversation_ai_state` e **traz o estado atual junto**.

O BACKFILL NÃO É OPCIONAL. Sem ele, no instante do deploy, todo lead que estava
pausado (por qualificação ou por handoff) volta a ser atendido pelo robô — por
cima de negociações que humanos assumiram. É uma migration de dados disfarçada de
migration de schema, e a parte de dados é a que importa.

Duas escolhas do backfill, ambas conservadoras:

  · **Uma linha por canal com agente.** `qualified_leads` é `(location_id, phone)`
    sem canal, e o portão que ficava no `ai_service` valia para TODOS os canais —
    então o efeito de hoje é "pausado em todo canal". Reproduzir isso exige o
    join com `ai_agents`. Passar a chave a ter canal é justamente o conserto
    (pausar o WhatsApp de um número pausava o Telegram dele), mas quem já estava
    pausado continua pausado onde estava.
  · **Sem prazo (`until` nulo).** As pausas antigas nasceram eternas. Inventar um
    vencimento retroativo religaria conversas silenciosamente — o oposto do que
    esta migration existe para evitar. O prazo passa a valer só para as novas.

`pausar_ao_qualificar` entra em `ai_agents` com default TRUE: o comportamento não
muda neste deploy. Ele muda quando o dono desligar a política, e aí é decisão
dele, num clique, não um efeito de migration.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "037"
down_revision: Union[str, None] = "036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABELA = "conversation_ai_state"
UQ = "uq_conv_ai_state"


def _tabela_existe(t: str) -> bool:
    return t in sa.inspect(op.get_bind()).get_table_names()


def _coluna_existe(t: str, c: str) -> bool:
    if not _tabela_existe(t):
        return False
    return c in {col["name"] for col in sa.inspect(op.get_bind()).get_columns(t)}


def upgrade() -> None:
    bind = op.get_bind()

    if not _coluna_existe("ai_agents", "pausar_ao_qualificar"):
        op.add_column(
            "ai_agents",
            sa.Column("pausar_ao_qualificar", sa.Boolean(), nullable=True,
                      server_default=sa.true()),
        )

    if not _tabela_existe(TABELA):
        op.create_table(
            TABELA,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("location_id", sa.String(),
                      sa.ForeignKey("tenants.location_id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("channel", sa.String(20), nullable=False, server_default="whatsapp"),
            sa.Column("contact_ref", sa.String(), nullable=False, index=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("motivo", sa.String(40), nullable=True),
            sa.Column("until", sa.DateTime(), nullable=True),
            sa.Column("mudado_por", sa.String(20), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
            sa.UniqueConstraint("location_id", "channel", "contact_ref", name=UQ),
        )

    # ── O backfill ──
    if not (_tabela_existe(TABELA) and _tabela_existe("qualified_leads")):
        return

    # Idempotente por construção: a migration roda no boot, em todo deploy. O
    # `NOT EXISTS` também protege quem já foi religado à mão depois do primeiro
    # deploy — religar não pode ser desfeito por rodar a migration de novo.
    sql = sa.text("""
        INSERT INTO conversation_ai_state
            (location_id, channel, contact_ref, enabled, motivo, until, mudado_por,
             created_at, updated_at)
        SELECT q.location_id,
               a.channel,
               q.phone,
               FALSE,
               CASE WHEN COALESCE(:handoff_expr, '') <> '' THEN 'handoff' ELSE 'qualificado' END,
               NULL,
               'sistema',
               COALESCE(q.created_at, :agora),
               :agora
          FROM qualified_leads q
          JOIN ai_agents a ON a.location_id = q.location_id
          JOIN tenants   t ON t.location_id = q.location_id
         -- A tabela-verdade ANTIGA, reproduzida em vez de suposta. Havia DOIS
         -- portões, e eles não cobriam o mesmo conjunto:
         --   · `inbound_pipeline.ai_is_enabled` pausava por qualified_lead SÓ no
         --     modo `whatsapp_only` (no modo CRM ia direto para o campo do GHL);
         --   · o portão do `ai_service` pausava em qualquer modo, mas SÓ quando
         --     `qualification_enabled` estava ligado.
         -- Sem este WHERE, o backfill calaria conversas que hoje estão no ar: um
         -- tenant com CRM que desligou a qualificação depois de ter leads antigos
         -- ficaria mudo no deploy, sem ninguém entender por quê.
         WHERE (COALESCE(t.mode, 'ghl') = 'whatsapp_only'
                OR COALESCE(a.qualification_enabled, FALSE) = TRUE)
           AND NOT EXISTS (
                   SELECT 1 FROM conversation_ai_state s
                    WHERE s.location_id = q.location_id
                      AND s.channel     = a.channel
                      AND s.contact_ref = q.phone
               )
    """)

    from datetime import datetime, timezone

    agora = datetime.now(timezone.utc)

    # O motivo (`handoff` x `qualificado`) mora dentro do JSON `qualified_data`, e
    # ler JSON em SQL portátil entre Postgres e SQLite não vale o risco numa
    # migration que derruba o boot se falhar. Backfill grava 'qualificado' para
    # todos e o handoff é corrigido logo abaixo, num UPDATE que pode falhar sem
    # consequência: o que importa é o `enabled=FALSE`, não o rótulo.
    inseridos = bind.execute(sql, {"handoff_expr": "", "agora": agora}).rowcount
    if inseridos:
        print(f"[037] {inseridos} conversa(s) pausada(s) preservadas do estado anterior.")

    if bind.dialect.name == "postgresql":
        try:
            corrigidos = bind.execute(sa.text("""
                UPDATE conversation_ai_state s
                   SET motivo = 'handoff'
                  FROM qualified_leads q
                 WHERE s.location_id = q.location_id
                   AND s.contact_ref = q.phone
                   AND s.motivo = 'qualificado'
                   AND s.mudado_por = 'sistema'
                   AND (q.qualified_data::jsonb ->> '_handoff') = 'true'
            """)).rowcount
            if corrigidos:
                print(f"[037] {corrigidos} pausa(s) reclassificada(s) como handoff.")
        except Exception as e:  # rótulo é cosmético; não vale derrubar o boot
            print(f"[037] Não foi possível reclassificar handoffs ({e}); seguem como 'qualificado'.")


def downgrade() -> None:
    if _tabela_existe(TABELA):
        op.drop_table(TABELA)
    if _coluna_existe("ai_agents", "pausar_ao_qualificar"):
        op.drop_column("ai_agents", "pausar_ao_qualificar")
