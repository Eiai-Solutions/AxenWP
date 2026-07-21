# Log de mensagens próprio — base do painel de chat

**Decisão (2026-07-20):** manter um log completo de mensagens no nosso Postgres
(tabela `messages`), separado da memória da IA, para embasar um futuro **painel de
chat próprio** (o cliente usar a plataforma em vez do CRM) e sustentar o modo
`whatsapp_only`.

## Por quê

Antes, o transcript completo só existia no **CRM (GHL)** — nós espelhávamos cada
mensagem para lá. No nosso banco havia só:
- `chat_histories` — a **memória da IA**: apenas turnos que a IA processou,
  mesclados pelo debounce, podados a cada 30 dias. Não é um log; é contexto do LLM.
- `message_mappings` — só `provider_id ↔ ghl_id` para status.

Consequência: no modo `whatsapp_only` (sem CRM), uma mensagem que a IA não
processou **não era gravada em lugar nenhum**. E um painel próprio não tinha de
onde ler o histórico. Ver [[axenwp-nao-usa-supabase]] — a persistência é no
Postgres do VPS, não em Supabase.

## Forma

Tabela **`messages`** (migration 026), append-only, **uma linha por mensagem
real**: cada mensagem do contato, **cada chunk** da resposta da IA, e o que o
operador digita no CRM. Colunas-chave: `direction`, `sender_role`
(`contact|ai|operator_crm|operator_panel|system`), `message_type`, `text`,
`media_filename` (→ `media_blobs`, ver [[whatsapp-waha]]), `provider_message_id`,
`ghl_message_id`, `status`, `contact_ref` + `ghl_contact_id`.

**Não** estende `chat_histories`: ciclos de vida opostos (memória volátil e podada
vs log durável). A IA continua lendo `chat_histories`; o painel lê `messages`.

## Como (choke point único)

`services/message_log.persist_message` — **best-effort** (nunca derruba o fluxo).
Dedup por **upsert**: casa por `provider_message_id`, senão `ghl_message_id`
(escopado por location), reforçado por **índices únicos parciais** no banco
(`uq_messages_provider_mid`, `uq_messages_ghl_mid`; parciais porque esses ids são
NULL em vários casos e NULLs não podem colidir) + tratamento de `IntegrityError`
para a corrida entre webhooks concorrentes.

Hooks nos pontos compartilhados:
- **inbound**: `handle_inbound` (WAHA/pipeline) e `process_inbound_message` (Z-API
  legado), **após** os filtros/dedup e **antes** do gate da IA — cobre
  `whatsapp_only` e IA desligada;
- **resposta da IA**: `_run_ai` e `_run_ai_response`, por chunk/áudio;
  `_mirror_outbound` devolve o `ghl_message_id` para completar a mesma linha;
- **operador no CRM**: `ghl_provider.process_outbound_message`, dedup por
  `ghl_message_id` (o GHL dispara `pending`+`sent` para o mesmo id);
- **status**: `process_status_update` espelha o status no log.

## Regras que o painel vai depender

- **Ordem do thread**: `ORDER BY id` (autoincrement é o tiebreaker — chunks saem
  com sleeps de 2-5s e `created_at` colide).
- **Uma resposta da IA = N linhas** (chunks). Se quiser agrupar o "turno", será um
  `turn_id` futuro.
- **Mídia**: priorizar `media_filename` → `media_blobs` (WAHA); cair para
  `media_url` (Z-API CDN público) com fallback.

## Lacunas conscientes

- **Telegram** inbound/outbound ainda **não** logado — entra quando o receiver
  migrar para o pipeline compartilhado (dívida já anotada no CLAUDE.md).
- **Áudio inbound**: `text` nasce com o rótulo (`🎤 Áudio recebido`); a transcrição
  real só existe pós-IA e não é feito backfill ainda.
- **Retenção**: `messages` é append-only e cresce sem limite (ao contrário do
  cleanup de `chat_histories`). Definir política antes de escalar.
