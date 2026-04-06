# AxenWP — Hub de Integracao WhatsApp

Backend que conecta GoHighLevel (GHL) CRM com Z-API (WhatsApp). Multi-tenant com OAuth 2.0, webhooks bidirecionais, auto-refresh de tokens e agentes de IA por tenant.

## Git e GitHub

Este projeto usa SSH para autenticacao no GitHub. O remote ja esta configurado.
- Para operacoes git (push, pull, fetch), use normalmente — o SSH cuida da autenticacao.
- Se precisar clonar ou adicionar um remote novo desta conta (Eiai-Solutions), use: `git@github-eiai:Eiai-Solutions/<repo>.git`
- NAO use URLs HTTPS do GitHub para remotes.

## Stack

- **Framework:** FastAPI 0.115 + Uvicorn (async)
- **Linguagem:** Python 3.11
- **ORM:** SQLAlchemy 2.0.35 + Alembic 1.13.3 (migrations)
- **Database:** PostgreSQL
- **HTTP:** httpx (async client)
- **Scheduler:** APScheduler 3.10.4
- **AI/LLM:** LangChain 1.2.10 (OpenRouter, ElevenLabs TTS, Groq Whisper)
- **Templates:** Jinja2 (admin UI server-side)
- **Testes:** pytest + pytest-asyncio
- **Deploy:** Docker (docker-compose)

## Estrutura

```
├── main.py              → FastAPI app + lifespan hooks
├── auth/
│   ├── oauth.py         → GHL OAuth flow
│   └── token_manager.py → Token refresh automatico (12h)
├── webhooks/
│   ├── ghl_provider.py  → GHL → Z-API (outbound)
│   └── zapi_receiver.py → Z-API → GHL (inbound)
├── services/
│   ├── ghl_service.py   → GoHighLevel API wrapper
│   └── zapi_service.py  → Z-API wrapper
├── admin/
│   ├── dashboard.py     → Tenant management UI
│   └── ai_agent.py      → Config de agente IA por tenant
├── data/
│   ├── models.py        → SQLAlchemy ORM models
│   ├── database.py      → Session factory
│   └── alembic/         → DB migrations
├── utils/
│   ├── config.py        → Pydantic Settings
│   └── logger.py        → Logging estruturado
├── web/templates/       → Jinja2 HTML
└── tests/               → pytest
```

## Tabelas principais

- `tenants` — Credenciais OAuth + configs Z-API (PK: location_id)
- `ai_agents` — Config LLM por tenant
- `knowledge_documents` — RAG docs para agentes IA
- `chat_histories` — Historico LangChain
- `contact_mappings` — Phone → GHL contact_id
- `message_mappings` — Z-API ↔ GHL message IDs
- `usage_logs` — Tracking de custos

## Comandos

```bash
# Dev
uvicorn main:app --reload --port 8000

# Testes
pytest

# Migrations
alembic upgrade head

# Docker
docker-compose up -d
```

## Startup Sequence

1. Alembic migrations (auto via lifespan)
2. APScheduler: token refresh (12h), chat cleanup (24h)
3. HTTP clients init (GHL, Z-API shared async)
4. Initial token refresh

## Convencoes

- Async-first: todos os services sao async, httpx.AsyncClient
- Type hints completos com Pydantic models
- Dependency Injection via FastAPI Depends
- Logging: `[timestamp] | [level] | [module] | [message]`
- HMAC cookies para admin auth, webhook secret validation

## Regras para o Claude

- Multi-tenant: toda operacao deve ser scoped por location_id
- Tokens GHL expiram em 24h — token_manager cuida do refresh a cada 12h
- Webhooks sao bidirecionais — mudancas em um lado afetam o outro
- Mensagens: sempre mapear IDs entre Z-API e GHL (message_mappings)
- Nunca armazenar tokens em plain text fora do banco
- Alembic migrations rodam automaticamente no startup — testar antes
- AI agents: config e por tenant, nunca compartilhar dados entre tenants
- httpx clients sao compartilhados (async) — nao criar novos por request
- Health check: `GET /health` retorna DB + tenants ativos
