---
titulo: Armazenamento no Supabase, processamento na VPS
status: solid
data: 2026-07-31
tags: [banco, infra, seguranca, supabase]
---

# Banco no Supabase — a VPS vira stateless

**Decisão:** o Postgres sai do Docker da VPS e passa a viver num projeto Supabase
(`sa-east-1`, mesma região da VPS). A VPS mantém só o processamento.

## Por que — e por que NÃO pelo motivo óbvio

O motivo intuitivo seria "backup gerenciado". Ele não se sustenta sozinho: o
Supabase Free **não tem backup agendado nenhum** (diário só a partir do Pro, PITR
é add-on caro), e um `pg_dump | gzip` num quarto job do APScheduler resolveria
"não perder tudo se a VPS morrer" por centavos, sem custo de latência.

O motivo que **de fato** justifica: com o estado fora, a VPS vira descartável —
reconstruível, movível, imune ao OOM que o `Dockerfile` documenta ter derrubado a
máquina quando o WAHA passou a dividi-la. Isso o `pg_dump` não dá.

## O risco que quase virou incidente

O Supabase expõe o schema `public` via PostgREST, e o **ACL padrão do projeto
concede `arwdDxtm` (leitura E escrita) ao role `anon`** em toda tabela nova
criada por `postgres`. A `anon` key é pública por design.

Um `pg_restore` no default teria publicado na internet: `tenants`
(access_token/refresh_token/pit_token/zapi_token OAuth do GHL), `ai_agents`
(chaves OpenRouter/Anthropic/ElevenLabs/FishAudio em texto claro),
`system_settings` (chaves globais) e `messages`/`chat_histories` (conversas de
clientes reais).

Verificado empiricamente antes de mover qualquer dado:
`SELECT ... FROM pg_default_acl` → `anon=arwdDxtm/postgres`, e
`GET /v1/projects/{ref}/postgrest` → `db_schema = public,graphql_public`.

**Mitigação, na ordem que importa:** a migração rodou como UMA transação —
schema → blindagem → dados. A blindagem antes do primeiro `INSERT`, para que os
segredos nunca ficassem numa tabela exposta, nem por um segundo.

```sql
REVOKE ALL ON SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
  REVOKE ALL ON TABLES FROM anon, authenticated;
ALTER TABLE public.<cada_tabela> ENABLE ROW LEVEL SECURITY;
```

Teste de aceite (roda a qualquer momento): com a anon key,
`GET /rest/v1/tenants` tem que devolver **401 / `42501`**.

## Role dedicado, não `postgres`

O app conecta como **`millochat_app`**, não como `postgres`. Não é preciosismo:
o Supabase pré-configurou default ACLs **para o role `postgres`** que concedem
tudo a `anon`. Como o app roda `create_all` a cada boot, conectar como `postgres`
faria **toda tabela futura nascer exposta** — uma mina permanente.

Tabelas criadas por `millochat_app` não têm default ACL para `anon`, então nascem
privadas. Verificado: tabela criada pelo app → anon recebe 401.

O role precisa de `BYPASSRLS` (o RLS está ligado sem policies) e de **`CREATE` no
schema** — só `USAGE` faz o `create_all` falhar no startup com
`permission denied for schema public`.

```sql
CREATE ROLE millochat_app LOGIN PASSWORD '...';
ALTER ROLE millochat_app BYPASSRLS;
GRANT USAGE, CREATE ON SCHEMA public TO millochat_app;
GRANT ALL PRIVILEGES ON ALL TABLES    IN SCHEMA public TO millochat_app;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO millochat_app;
```

## Conexão: pooler, não direta

A conexão direta (`db.<ref>.supabase.co:5432`) é **IPv6-only**. O host da VPS tem
IPv6, mas o **container Docker não tem rota IPv6** — de dentro do container dá
`Network is unreachable`.

Usar o Supavisor em **session mode**: `aws-0-sa-east-1.pooler.supabase.com:5432`,
usuário `millochat_app.<ref>`, `sslmode=require`. Session mode (não o transaction
mode da 6543) porque o SQLAlchemy mantém pool persistente e o Alembic precisa de
sessão real.

## Latência medida (não estimada)

| | 50 queries | por round-trip |
|---|---|---|
| Postgres local | 63 ms | ~1,3 ms |
| Supabase (pooler, mesma região) | 280 ms | ~5,6 ms |

O caminho quente de uma mensagem inbound abre ~20 `SessionLocal()` e faz ~45
round-trips ⇒ **~195 ms a mais por mensagem**. Imperceptível: o mesmo turno já
embute debounce em segundos, `asyncio.sleep` entre chunks e 2-4 s de LLM.

**A latência só vira catastrófica se as regiões não forem co-locadas** —
cross-continente (~120 ms) daria ~5,4 s por mensagem. Manter VPS e projeto
Supabase na mesma região é condição, não otimização.

## O que a migração obrigou a corrigir no código

`data/database.py` não tinha config de pool nenhuma. Contra banco local isso
nunca doeu — a conexão nunca morre sozinha. Contra um pooler gerenciado, conexão
ociosa é derrubada, volta morta do pool e estoura `OperationalError`. Como
`grep -rniE "OperationalError|tenacity|retry"` retorna **zero** no projeto, não
há nada para absorver: vira 500 no webhook e mensagem de lead perdida. É
regressão de **corretude**, não de performance. Agora: `pool_pre_ping=True`,
`pool_recycle=300`, `pool_size=5`, `max_overflow=5`, keepalives e TLS.

Fechado também um alçapão: `settings.database_url` tem **default de SQLite**. Se
a env var sumisse em produção, o app subia num banco vazio e `/health` respondia
`healthy` — todos os tenants sumiam sem um erro no log. Agora recusa subir.

## Rollback

Trocar `DATABASE_URL` de volta e reiniciar. **O Postgres da VPS não foi deletado**
— é o rollback, e a reversibilidade só existe enquanto ele existir. Deletar junto
com o corte trocaria uma migração reversível por uma irreversível de graça.
Manter parado por ~30 dias, com um restore de teste comprovado, e só então apagar.

## Pendência operacional

A `DATABASE_URL` foi trocada no nível do **Docker Swarm**, não no store do
EasyPanel (LMDB binário, sem API acessível). **O próximo deploy pelo EasyPanel
reverte a variável** e o app volta silenciosamente ao Postgres da VPS, dividindo
os dados. Replicar em EasyPanel → serviço `servidorwp` → Environment.

Ver [[log-de-mensagens]] e [[produto-saas-fase0]].
