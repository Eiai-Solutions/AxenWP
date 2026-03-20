# AxenWP — Fix Plan

Auditoria completa do codebase. 40 tarefas reais organizadas por prioridade.
Gerado em: 2026-03-20

---

## P0 — CRITICO (Seguranca / Data Loss)

### 1. CORS permissivo demais
- **Arquivo:** `main.py:79-85`
- **Problema:** `allow_origins=["*"]` com `allow_credentials=True` permite qualquer site fazer requests autenticados
- **Risco:** CSRF, acesso nao autorizado
- **Fix:** Restringir origins para dominio(s) especifico(s) via env var `ALLOWED_ORIGINS`

### 2. [DONE] Senha admin padrao hardcoded
- **Arquivo:** `admin/dashboard.py:23`
- **Problema:** `ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")` — se .env nao for configurado, admin fica aberto com senha trivial
- **Risco:** Acesso nao autorizado ao painel admin
- **Fix:** Exigir variavel de ambiente; falhar no startup se nao definida
- **Implementado:** `_get_admin_password()` + `settings.admin_password` — RuntimeError em producao se nao definido, fallback em DEBUG, warning para senhas fracas

### 3. Webhooks sem validacao de assinatura
- **Arquivos:** `webhooks/zapi_receiver.py:318-340`, `webhooks/ghl_provider.py:270-291`
- **Problema:** Endpoints de webhook aceitam qualquer POST sem verificar assinatura HMAC
- **Risco:** Atacante pode enviar mensagens falsas, manipular contatos, disparar IA
- **Fix:** Implementar verificacao HMAC-SHA256 para Z-API e GHL

### 4. API Keys expostas no HTML
- **Arquivo:** `admin/dashboard.py:118-132`
- **Problema:** `api_key`, `elevenlabs_api_key`, `groq_api_key` passadas integrais para o template Jinja2
- **Risco:** Credenciais visiveis no source da pagina ou em screenshots
- **Fix:** Passar apenas versoes mascaradas (primeiros 4 + ultimos 4 chars) para exibicao; manter valores reais apenas nos inputs hidden

### 5. Payload sensivel logado em texto puro
- **Arquivo:** `webhooks/ghl_provider.py:279`
- **Problema:** `logger.info(f"PAYLOAD BRUTO RECEBIDO DO GHL: {payload_dict}")` loga dados de clientes
- **Risco:** Vazamento de PII em logs
- **Fix:** Logar apenas campos nao-sensiveis; remover log de payload bruto

### 6. OAuth secrets em memoria sem TTL
- **Arquivo:** `auth/oauth.py:17-18`
- **Problema:** `_temp_oauth_secrets = {}` sem expiracao ou limpeza
- **Risco:** Vazamento de credenciais; memory leak
- **Fix:** Adicionar TTL de 10min e limpeza automatica

---

## P1 — ALTO (Bugs / Estabilidade)

### 7. Race condition no debounce de IA
- **Arquivo:** `webhooks/zapi_receiver.py:26-28`
- **Problema:** Dicionarios globais `_ai_pending_tasks`, `_ai_message_buffers` nao sao thread-safe
- **Risco:** Mensagens duplicadas ou perdidas em alta concorrencia
- **Fix:** Usar `asyncio.Lock` por contact_key

### 8. Task pendente nunca limpa em caso de erro
- **Arquivo:** `webhooks/zapi_receiver.py:31-137`
- **Problema:** Se excecao ocorre em `_run_ai_response`, `_ai_pending_tasks` nao e limpo (pop esta no try, nao no finally)
- **Risco:** Memory leak; tasks fantasma acumulam
- **Fix:** Mover `_ai_pending_tasks.pop(contact_key, None)` para bloco `finally`

### 9. Sem retry/backoff no LLM
- **Arquivo:** `services/ai_service.py:217-278`
- **Problema:** `llm.ainvoke()` pode falhar por timeout sem retry
- **Risco:** Mensagens de clientes perdidas silenciosamente
- **Fix:** Implementar retry com backoff exponencial (max 3 tentativas)

### 10. N+1 HTTP calls no dashboard
- **Arquivo:** `admin/dashboard.py:105-141`
- **Problema:** Para cada tenant, `zapi_service.get_status()` faz chamada HTTP sequencial
- **Risco:** Dashboard lento/timeout com muitos tenants
- **Fix:** Fazer chamadas em paralelo com `asyncio.gather()`

### 11. Hot reload habilitado em producao
- **Arquivo:** `main.py:133`
- **Problema:** `reload=True` hardcoded no uvicorn
- **Risco:** App reinicia a cada mudanca de arquivo em producao
- **Fix:** Condicionar a `DEBUG` env var: `reload=settings.debug`

### 12. Alembic falha silenciosamente no startup
- **Arquivo:** `main.py:45-54`
- **Problema:** Se migracao falha, app inicia assim mesmo com schema incompleto
- **Risco:** Endpoints falham com erros de coluna inexistente
- **Fix:** Fail-fast se `alembic upgrade head` falhar

### 13. Token refresh a cada 12h com janela de vulnerabilidade
- **Arquivo:** `main.py:56-62`
- **Problema:** Se token expira entre hora 11 e 12, nao sera renovado a tempo
- **Risco:** Chamadas GHL falham por token expirado
- **Fix:** Refresh a cada 1h ou implementar refresh on-demand no `ghl_service`

### 14. Speed 0.0 pode chegar ao ElevenLabs
- **Arquivo:** `services/ai_service.py:260`
- **Problema:** `float(self.agent_config.elevenlabs_speed or 1.0)` — se valor no DB for `0.0`, `or` nao pega (0.0 e falsy)
- **Risco:** ElevenLabs rejeita speed=0
- **Fix:** `max(0.25, float(self.agent_config.elevenlabs_speed if self.agent_config.elevenlabs_speed is not None else 1.0))`

### 15. Deteccao de contato deletado depende de string magica
- **Arquivo:** `webhooks/zapi_receiver.py:244-268`
- **Problema:** Verifica `"Contact not found/deleted"` no body — se GHL mudar msg, quebra
- **Risco:** Contatos deletados nunca recriados; mensagens perdidas
- **Fix:** Verificar por HTTP status code (400/404) + campo estruturado do response

---

## P2 — MEDIO (Qualidade / Robustez)

### 16. Sem validacao de tamanho nos inputs
- **Arquivo:** `admin/ai_agent.py:21-36`
- **Problema:** `name` e `prompt` sem limite de tamanho
- **Risco:** DoS / saturacao do banco
- **Fix:** Validacao: prompt max 50000 chars, name max 255

### 17. Sem rate limiting
- **Arquivo:** Projeto inteiro
- **Problema:** Nenhum endpoint tem rate limiting
- **Risco:** DoS, spam, abuso de API
- **Fix:** Middleware `slowapi` no FastAPI (ex: 60 req/min para webhooks, 10 req/min para admin)

### 18. Phone formatting inconsistente
- **Arquivos:** `ghl_service.py:57-60`, `zapi_service.py:181-186`
- **Problema:** GHL adiciona `+`, Z-API strip non-digits — podem gerar session_id diferente para mesmo contato
- **Risco:** Contatos duplicados; historico de chat fragmentado
- **Fix:** Centralizar em `utils/phone.py` com funcao unica de normalizacao

### 19. Regex de split de mensagem fragil
- **Arquivo:** `webhooks/zapi_receiver.py:99-103`
- **Problema:** Regex compilado a cada mensagem; acentos PT podem nao matchear no lookahead
- **Risco:** Mensagens nao divididas corretamente
- **Fix:** Pre-compilar regex no modulo; adicionar chars acentuados

### 20. Chat history sem pruning
- **Arquivo:** `services/ai_service.py:100`
- **Problema:** `max_history = 20` carrega ultimas 20, mas tabela cresce infinitamente
- **Risco:** Banco incha; queries lentas
- **Fix:** Job periodico para deletar historico > 30 dias (ou configuravel por tenant)

### 21. Status Z-API mapeamento incompleto
- **Arquivo:** `webhooks/zapi_receiver.py:367-374`
- **Problema:** Apenas DELIVERED/READ/ERROR mapeados; outros caem em "delivered" como default
- **Risco:** Status incorreto no GHL
- **Fix:** Mapear PENDING, SENT, FAILED_TO_SEND; logar status desconhecidos

### 22. Timeouts hardcoded em todos os servicos
- **Arquivos:** Multiplos (10.0, 30.0, 60.0, 90.0 espalhados)
- **Problema:** Nao configuravel; inconsistente
- **Fix:** Centralizar em `config.py`: `TIMEOUT_DEFAULT`, `TIMEOUT_LLM`, `TIMEOUT_TTS`

### 23. Custom fields cache sem invalidacao
- **Arquivo:** `services/ghl_service.py:18-20`
- **Problema:** Cache nunca expira
- **Fix:** TTL de 1h no cache com `time.time()` check

### 24. SystemSettings consultado do DB a cada analise
- **Arquivo:** `admin/ai_agent.py`
- **Fix:** Cache in-memory com invalidacao ao salvar settings

### 25. Codigo duplicado no editor de prompts
- **Arquivo:** `admin/ai_agent.py` (analyze-prompt e master-chat tem mesma etapa 3)
- **Fix:** Extrair para `async def _apply_changes_to_prompt(client, headers, model, original, changes)`

### 26. Sem request ID para debugging
- **Arquivo:** Projeto inteiro
- **Problema:** Impossivel correlacionar logs de uma mesma request
- **Fix:** Middleware que gera UUID por request; injetar em todos os logs via `logging.Filter`

---

## P3 — BAIXO (Melhorias / Infraestrutura)

### 27. Sem testes automatizados
- **Fix:** Pytest + httpx.AsyncClient para fluxos criticos (webhook inbound, AI response, OAuth flow)

### 28. Pool de conexoes DB nao configurado
- **Arquivo:** `data/database.py:16-18`
- **Fix:** `pool_size=10, max_overflow=20, pool_pre_ping=True`

### 29. Docker sem resource limits
- **Arquivo:** `docker-compose.yml`
- **Fix:** `deploy.resources.limits` (mem 512M, cpu 1.0) + `restart: unless-stopped`

### 30. Volume mount nao utilizado
- **Arquivo:** `docker-compose.yml:11`
- **Fix:** Remover `./data/tenants:/app/data/tenants`

### 31. Health check superficial
- **Arquivo:** `main.py:109-128`
- **Fix:** Adicionar ping DB + check token GHL valido

### 32. Sem graceful shutdown de tasks
- **Arquivo:** `main.py:59-67`
- **Fix:** Aguardar `_ai_pending_tasks` com timeout de 30s no shutdown

### 33. Sem backup automatico do banco
- **Fix:** Cron pg_dump diario para S3

### 34. .env.example incompleto
- **Fix:** Documentar ADMIN_PASSWORD, ALLOWED_ORIGINS, ZAPI_WEBHOOK_SECRET

### 35. Emojis em logs
- **Arquivo:** `webhooks/zapi_receiver.py:57`
- **Fix:** Substituir por prefixos texto: `[DEBOUNCE]`, `[AI]`, `[WEBHOOK]`

### 36. Import duplicado
- **Arquivo:** `webhooks/ghl_provider.py:10`
- **Fix:** Remover import duplicado de `Optional, List`

### 37. Formato de resposta inconsistente
- **Fix:** Padronizar: `{"success": bool, "data": ..., "error": str|null}`

### 38. Sem audit logging
- **Fix:** Log de acoes admin (salvar agente, alterar settings) com timestamp + IP

### 39. NaN/Infinity aceitos em floats
- **Arquivo:** `admin/ai_agent.py:29-31`
- **Fix:** `if not math.isfinite(val): val = default`

### 40. Sem suporte a multiplos agentes por tenant
- **Arquivo:** `data/models.py`
- **Fix:** Futuro — mudar relacao AIAgent para 1:N com campo `is_default`

---

## Estatisticas

| Prioridade | Qtd | Descricao |
|------------|-----|-----------|
| **P0** | 6 | Seguranca critica |
| **P1** | 9 | Bugs / estabilidade |
| **P2** | 11 | Qualidade / robustez |
| **P3** | 14 | Melhorias / infra |
| **Total** | **40** | |

## Ordem sugerida de execucao

**Sprint 1 (Seguranca):** P0 items 1-6
**Sprint 2 (Estabilidade):** P1 items 7-15
**Sprint 3 (Qualidade):** P2 items 16-26
**Sprint 4 (Infra):** P3 items 27-40

## Completed

- [x] Project audited (2026-03-20)
- [x] #1 CORS permissivo — adicionado ALLOWED_ORIGINS env var + DEBUG mode fallback (2026-03-20)
- [x] #11 Hot reload em producao — condicionado a DEBUG env var (2026-03-20)
- [x] #2 Senha admin hardcoded — _get_admin_password() com fail-fast em producao + weak password warning (2026-03-20)
