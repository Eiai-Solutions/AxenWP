# AxenWP — Hub de Integração WhatsApp + GHL + Telegram

Backend FastAPI que conecta GoHighLevel CRM com WhatsApp (Z-API) e Telegram, multi-tenant, com agentes de IA por canal, qualificação automática de leads e geração de prompts via IA Mestre.

## Git e GitHub

Repositório: `Eiai-Solutions/AxenWP` via SSH.
- Operações git via SSH (chave já configurada).
- Se adicionar remote novo: `git@github-eiai:Eiai-Solutions/<repo>.git`.
- Nunca use URL HTTPS do GitHub para remotes.

## Stack

- **Framework:** FastAPI 0.115 + Uvicorn (async)
- **Linguagem:** Python 3.11
- **ORM:** SQLAlchemy 2.0.35 + Alembic (migrations idempotentes)
- **Database:** PostgreSQL (produção) / SQLite (dev)
- **HTTP:** httpx (async, clients compartilhados em singleton)
- **Scheduler:** APScheduler 3.10
- **LLM:** LangChain + OpenRouter (multi-provider). ElevenLabs TTS, Groq Whisper STT.
- **Templates:** Jinja2 com partials + JS/CSS estáticos em `web/static/`
- **Rate limit:** slowapi
- **Testes:** pytest + pytest-asyncio (90+ tests)
- **Deploy:** Docker / EasyPanel (Hostinger)

## Estrutura atual

```
├── main.py                          → FastAPI app + lifespan + scheduler
├── auth/
│   ├── oauth.py                     → GHL OAuth flow
│   └── token_manager.py             → Refresh 12h + tenant CRUD + PIT support
├── webhooks/
│   ├── ghl_provider.py              → GHL → Z-API (outbound do CRM)
│   ├── zapi_receiver.py             → Z-API → engine (debounce + dedup + qualif)
│   └── telegram_receiver.py         → Telegram → engine (debounce + qualif, paritário com Z-API)
├── services/
│   ├── ai_service.py                → AIEngine (orquestrador) + AIService (cache)
│   ├── audio_handler.py             → STT (Groq) + TTS (ElevenLabs) + heurística
│   ├── chat_memory.py               → PostgresChatMessageHistory
│   ├── prompt_builder.py            → build_system_prompt (base + qualif + audio mode)
│   ├── prompt_history.py            → snapshot/list/restore versões do prompt
│   ├── qualification_engine.py      → extrai marcador, gera resumo, cache de progresso
│   ├── qualification_handler.py     → cria opportunity GHL + persiste QualifiedLead
│   ├── usage_logger.py              → save_usage_log (OpenRouter/Groq/ElevenLabs)
│   ├── ghl_service.py               → GoHighLevel API wrapper
│   ├── zapi_service.py              → Z-API wrapper (text/audio/image/webhook)
│   └── telegram_service.py          → Telegram Bot API wrapper
├── admin/
│   ├── dashboard.py                 → UI server-side + endpoints de tenant
│   ├── ai_agent.py                  → CRUD agente, prompt-history, improve-prompt
│   ├── diagnostics.py               → Endpoints de debug (gated por DEBUG_ENDPOINTS_ENABLED)
│   └── seed_joorney.py              → Seed da demo Joorney (idempotente)
├── public/
│   └── onboarding.py                → Form público /form/{token}/submit (rate-limited)
├── data/
│   ├── models.py                    → SQLAlchemy ORM
│   └── database.py                  → engine + SessionLocal
├── alembic/versions/                → 18 migrations
├── utils/
│   ├── config.py                    → Pydantic Settings (.env)
│   ├── logger.py                    → Logging estruturado
│   ├── master_prompt.py             → IA Mestre v2 (register-aware)
│   ├── agent_validators.py          → Pydantic validators do AIAgent
│   ├── guardrails.py                → forbidden phrases, sentiment, placeholders
│   ├── validators.py                → location_id, form_token, phone (regex)
│   ├── limiter.py                   → slowapi singleton (key_func=get_remote_address)
│   └── metrics.py                   → counters in-memory + Prometheus exposition
├── web/
│   ├── templates/dashboard.html     → 1716 linhas (JS/CSS extraídos)
│   ├── templates/onboarding_form.html
│   └── static/{js,css}/             → dashboard.js (defer) + dashboard.css
└── tests/                           → 90 tests (audio_handler, validators, metrics, master_prompt)
```

## Tabelas principais

- `tenants` — credenciais OAuth + Z-API + Telegram + PIT (PK: location_id)
- `ai_agents` — config LLM/voz por (location_id, channel). `linked_to_channel` para alias.
- `agent_prompt_history` — versionamento (form/regenerate/optimize_apply/manual_save/restore)
- `chat_histories` — memória de conversa (location_id explícito + index composto)
- `qualified_leads` — leads qualificados (1 por phone+location, flag pra IA)
- `knowledge_documents` — docs para RAG (não implementado ainda)
- `contact_mappings` — phone/@lid ↔ ghl_contact_id
- `message_mappings` — zapi_message_id ↔ ghl_message_id
- `usage_logs` — tracking de uso (openrouter, groq, elevenlabs)
- `system_settings` — chaves globais (admin OpenRouter + admin Groq, single row)

## Convenções

- **Async-first**: todos os services são async, httpx.AsyncClient compartilhado.
- **Sync DB → async**: queries SQLAlchemy correm via `asyncio.to_thread()`.
- **Multi-tenant**: tudo escopado por `location_id`. Sessions de chat: `f"{location_id}_{phone}"`.
- **Multi-canal**: agente por `(location_id, channel)`. `linked_to_channel` aliasa canais (ex: telegram usa config do whatsapp).
- **Validação na borda**: regex em `utils/validators.py` rejeita location_id/form_token/phone malformados antes de queries.
- **Buffers in-memory**: `deque(maxlen=N)` ou `OrderedDict` com cap. Cleanup periódico via APScheduler.
- **Logging**: `[timestamp] | [level] | [module] | [message]`. Tags `[AUDIO]`, `[TTS-DECISION]`, `[MEMORY]` para grep rápido.
- **Auth admin**: HMAC cookie sobre `ADMIN_PASSWORD`. Diagnostics adicionalmente gated por env.

## IA Mestre v2 — geração e melhoria de prompts

`utils/master_prompt.py` é o cérebro que gera prompts dos agentes a partir do `form_data`.

- **Registro detectado** automaticamente (`premium`, `casual`, `support`, `neutro`) via keywords no industry/audience/products. Operador pode forçar via `form_data["tone_register"]`.
- **Estrutura adaptativa**: SUPPORT não recebe seção de objeções; OUTBOUND exige 2-3 variantes de abertura; INBOUND faz SPIN+BANT.
- **Output 300-700 palavras**, denso. Output longo dilui regras.
- Dois modos: `build_messages(form_data)` para gerar e `build_improve_messages(form_data, current_prompt, history, mode, feedback)` para diagnose/apply contextual.

## Guardrails de runtime

`utils/guardrails.py` filtra a resposta do LLM antes do envio:
- `strip_emojis()` — sempre aplicado.
- `contains_forbidden_phrase(mode="outbound")` — regenera se o agente disser "como posso te ajudar?" em modo outbound.
- `contains_placeholder()` — força regenerar se vier `[NOME]`, `{empresa}`, `<X>`.
- `should_escalate()` — flag `escalate=True` no result quando detecta frustração ou pedido de humano.

## Comandos

```bash
# Dev local
uvicorn main:app --reload --port 8000

# Testes
pytest -q

# Migrations (rodam auto no startup, mas pra debug local)
alembic upgrade head
alembic revision -m "descricao"

# Docker / produção
docker-compose up -d
```

## Startup sequence (lifespan)

1. `Base.metadata.create_all` (tabelas novas idempotente).
2. Alembic `upgrade head` (adiciona colunas, índices).
3. APScheduler:
   - `refresh_tokens_job` a cada 12h (tokens OAuth do GHL)
   - `cleanup_old_chat_history` a cada 24h (>30 dias)
   - `cleanup_stale_debounce_entries` a cada 10min (Z-API)
   - `cleanup_stale_telegram_debounce` a cada 10min
4. HTTP clients init: `ghl_service`, `zapi_service`, `telegram_service`.
5. Initial token refresh + cleanup.

## Variáveis de ambiente importantes

- `DATABASE_URL` — Postgres em prod, SQLite em dev
- `ADMIN_PASSWORD` — obrigatório fora de DEBUG
- `GHL_CLIENT_ID` / `GHL_CLIENT_SECRET` / `GHL_REDIRECT_URI`
- `PUBLIC_BASE_URL` — usado para registrar webhooks externos (Z-API/Telegram)
- `ALLOWED_ORIGINS` — CORS (vazio fora de DEBUG bloqueia cross-origin)
- `ZAPI_WEBHOOK_SECRET` — validação Z-API (opcional)
- `DEBUG_ENDPOINTS_ENABLED` — `false` em prod desliga `/admin/diagnostics/*`

## Endpoints chave

- `GET /health` — status + agents_active + qualified_leads + métricas resumidas
- `GET /metrics` — Prometheus exposition format (text/plain)
- `POST /webhook/zapi/inbound/{location_id}` — rate 120/min, validado
- `POST /webhook/zapi/status/{location_id}` — rate 240/min
- `POST /webhook/telegram/{location_id}` — rate 120/min
- `POST /form/{token}/submit` — rate 5/min, valida token regex
- `GET /admin/agents/{id}/prompt-history?channel=X` — lista versões
- `POST /admin/agents/prompt-history/{id}/restore` — restaura versão
- `GET /admin/diagnostics/audio-pipeline/{location_id}` — pipeline E2E (gated)

## Regras para o Claude

- **Sempre scoped por location_id**. `session_id = location_id + "_" + phone`.
- **Não armazenar tokens em plain text fora do banco**. SystemSettings só admite chaves globais (Groq STT, OpenRouter Mestre).
- **PIT vs OAuth**: PIT no `tenant.pit_token` (não expira); OAuth normal usa `access_token`+`refresh_token`. `get_valid_token` prioriza PIT se houver.
- **Migrations idempotentes**: usam helpers `_column_exists`/`_table_exists`/`_index_exists`. Rodam no startup; testar localmente antes.
- **httpx clients são singletons** (`ghl_service`, `zapi_service`, `telegram_service`). Não criar novos por request.
- **AI agents nunca compartilham dados entre tenants**. Se o canal é alias (`linked_to_channel`), resolve no AIService antes de carregar engine.
- **Snapshot de prompt** sempre que `agent.prompt` é escrito: `services/prompt_history.snapshot_prompt()`.
- **Diagnostics**: novos endpoints de debug devem ir em `admin/diagnostics.py` (gated), não em `seed_joorney.py`.
- **Validators na borda**: qualquer endpoint público novo passa por `is_valid_location_id`/`is_valid_form_token` antes de tocar o banco.
