---
type: decisao
status: solid
updated: 2026-07-14
sources: [webhooks/zapi_receiver.py, webhooks/telegram_receiver.py, services/ai_service.py, services/zapi_service.py, data/models.py, services/chat_memory.py]
confidence: high
---

# Plano de reestruturação — Abstrações-primeiro (ChannelAdapter + AgentEngine)

Backlog da reestruturação que transforma **duas trocas grandes** — Z-API → **WAHA** ([[decisoes/whatsapp-waha]]) e LangChain single-turn → **Claude Agent SDK** ([[decisoes/agente-claude-agent-sdk]]) — em **plugues**, não rewrites. Regra de ouro: introduzir as **portas** antes de trocar qualquer implementação.

> ⚠️ **Achado bloqueante:** hoje **não existe golden test** de `process_incoming_message` — `tests/test_ai_service.py` só cobre `contains_special_content`. "Suíte verde" é prova vazia de paridade. **Passo 0 (rede de caracterização) é pré-requisito e portão de merge de tudo.**

## 1. Princípio: abstrações-primeiro
A lógica provider-agnóstica (parse, filtro grupo/`fromMe`, dedup, debounce, registro-contato-GHL, gating-IA, qualificação, chunking, envio, status) está entrelaçada com Z-API em `webhooks/zapi_receiver.py:239` (`process_inbound_message`) e `:119` (`_run_ai_response`), e **duplicada** em `webhooks/telegram_receiver.py:45-229`. O único ponto que fala LLM é **uma linha**: `services/ai_service.py:305`.

Introduzir duas **fronteiras** primeiro (`ChannelAdapter` para I/O de canal, `AgentEngine` para a chamada LLM) faz cada troca virar **um adapter novo atrás de flag desligada**. As portas são **ortogonais** — o pipeline só chama `engine.process_incoming_message(...)` (`ai_service.py:440`, já `channel`-ready). Canal e motor são I/O ao redor do **mesmo miolo preservado**: `session_id=f"{location_id}_{phone}"` (`chat_memory.py:18`), memória 20 msgs (`chat_memory.py:26`), IA Mestre v2 (`utils/master_prompt.py`), versionamento de prompt (`prompt_history.py`), guardrails, qualificação, sync GHL.

**As 4 combinações coexistem:** (zapi|waha) × (langchain|claude), cada uma revertível sozinha.

## 2. Abstração 1 — ChannelAdapter + WAHA

### 2.1 A porta (`channels/base.py`, NOVO)
`ParsedMessage` normalizado: `channel, provider, location_id, sender_id` (sem `@c.us`/`@s.whatsapp.net`/`@lid`), `provider_message_id` (== `zapiMessageId` hoje, `zapi_receiver.py:259`), `text, is_audio, audio_url, attachments, is_group, from_me, sender_name, event_kind, raw`. `OutboundResult(ok, provider_message_id, error)`. `ChannelCapabilities(supports_audio_ptt, supports_typing_delay, outbound_media_accepts_data_url, provider_reechoes_own_msgs)`.

```python
class ChannelAdapter(Protocol):
    channel: str; provider: str; capabilities: ChannelCapabilities
    def parse_inbound(self, location_id, payload, headers) -> ParsedMessage | None: ...
    async def send_text(self, tenant, to, text, *, typing_delay=0) -> OutboundResult: ...
    async def send_image(self, tenant, to, image_url, caption="") -> OutboundResult: ...
    async def send_audio(self, tenant, to, audio_data_url) -> OutboundResult: ...  # data:audio/ogg;base64,...
    def credentials_ok(self, tenant) -> bool: ...
    async def register_webhook(self, tenant, public_base_url) -> bool: ...

# channels/registry.py
def resolve_whatsapp_adapter(tenant):
    return WAHAChannel() if getattr(tenant,"whatsapp_provider","zapi")=="waha" else ZAPIChannel()
```

**Decisão-chave:** WhatsApp é um **CANAL** (`'whatsapp'`) com dois **PROVEDORES** (`'zapi'|'waha'`) sub-selecionados por tenant. Preserva `session_id`, `AIAgent.channel=='whatsapp'` (`models.py:73`) e memória/prompt/qualificação — WAHA e Z-API **compartilham o mesmo agente**.

### 2.2 Pipeline compartilhado único (`channels/pipeline.py`, NOVO)
Concentra **uma vez** o fluxo de `zapi_receiver.py:239-492` + `:119-236` (hoje duplicado no Telegram). Relocar os dicts de debounce (`:30-32`) e dedup (`:62-83`); re-chavear `contact_key = f"{channel}:{location_id}:{sender_id}"` **preservando exatamente o par (location, sender)** (hoje `:450`). `handle_inbound(adapter, location_id, payload, headers)`: parse → guard `event_kind/is_group/from_me` → dedup por `provider_message_id` (`:275`) → se `mode != whatsapp_only` registra contato GHL (`:355-420`) → `_debounce_schedule`. `_run_debounced`: sleep(debounce) → junta buffer → `ai_service.process_incoming_message(..., channel="whatsapp")` (SEAM) → se `qualified_data` chama `qualification_handler.handle_qualification` → chunking idêntico (`re.split(r'\n\n+', ...)`, `:198`) + delays → `adapter.send_text` por chunk → `_track_sent` (dedup) + `save_message_mapping` → `adapter.send_audio` se houver.

**Rota universal nova** `POST /webhook/whatsapp/{location_id}` (`webhooks/whatsapp_router.py`) resolve o adapter e chama o pipeline; nunca derruba o request (padrão `zapi_receiver.py:525`). **Rota Z-API legada permanece byte-idêntica** (`zapi_receiver.py:495`, mesma URL, mesmo 200), só passando a delegar `pipeline.handle_inbound(ZAPIChannel(), ...)`.

### 2.3 Adapters concretos
- **`ZAPIChannel`** (`channels/whatsapp/zapi.py`) — envelopa o `zapi_service` atual sem reescrever. `parse_inbound` normaliza as variantes inline de `zapi_receiver.py:303-347` + filtros `:255-282`. `send_*` delega a `zapi_service.py:80/103/150` (send_audio já faz PTT). `capabilities.provider_reechoes_own_msgs=False`, `outbound_media_accepts_data_url=True`.
- **`WAHAChannel`** (`channels/whatsapp/waha.py`, NOVO) — REST com sessão por tenant + header `X-Api-Key`. `capabilities.provider_reechoes_own_msgs=True` (**WAHA re-echoa `fromMe` → dedup obrigatório**), `outbound_media_accepts_data_url=False` (**base64 em `file.data`**).

| Ação | WAHA REST | Notas |
|---|---|---|
| Texto | `POST {base}/api/sendText` `{session, chatId, text}` | typing via `/api/startTyping`+`/stopTyping` |
| Imagem | `POST {base}/api/sendImage` `{session, chatId, file:{url\|data,mimetype,filename}, caption}` | — |
| Voz/PTT | `POST {base}/api/sendVoice` `{session, chatId, file:{mimetype:"audio/ogg; codecs=opus", data:"<base64>"}}` | `data.split(',',1)[1]` — não aceita data-url |
| Registrar webhook | `PUT/POST {base}/api/sessions/{session}` com `config.webhooks` → `/webhook/whatsapp/{location_id}`, events `["message","message.ack","session.status"]` | `public_base` = `settings.public_base_url` (`config.py:56`) |
| Status/QR | `GET /api/sessions/{session}` + `GET /api/{session}/auth/qr` | paralelo a `zapi_service.get_status/get_qr_code` |

**Parse varia por engine** (`Tenant.waha_engine` guia): NOWEB pode ter `id` como dict serializado e `media.url` faltando (fallback `/api/{session}/files` ou download via `media.id`); GOWS/WEBJS diferem em `_serialized`/`ack`. `WAHAService` (`services/waha_service.py`) espelha `zapi_service.py:19-33` com `startup()/shutdown()` do httpx client, em `main.py:104/114`.

- **`TelegramChannel`** (`channels/telegram.py`) — refactor de `telegram_receiver.py:45-229` para a mesma porta, matando o pipeline duplicado. **Deferível** para depois do canário WAHA.

### 2.4 Config por tenant (`Tenant`, `models.py:18-21`, aditivo)
`whatsapp_provider` (default `'zapi'`, `server_default`, nullable=False), `waha_base_url`, `waha_session` (default sugerido = `location_id`), `waha_engine` (`'NOWEB'|'GOWS'|'WEBJS'`), `waha_api_key` (**cifrado**, gate WS1). `MessageMapping` **reusa a coluna `zapi_message_id`** (`models.py:188`) — sem migration destrutiva.

## 3. Abstração 2 — AgentEngine + Claude Agent SDK

### 3.1 A porta (`services/agent_engine/base.py`, NOVO)
Isola o único ponto que fala LLM (`ai_service.py:305`). Tudo ao redor fica no pipeline (`AIEngine.generate_response`, `:249-380`, vira orquestrador engine-agnóstico).

```python
@dataclass
class AgentContext:
    location_id; channel; session_id; user_phone
    system_prompt: str          # já montado por build_system_prompt (IA Mestre + qualif + audio)
    history: list[dict]         # 20 msgs
    incoming_text: str          # combined_text do debounce, já transcrito
    is_audio_input: bool
    tools: list[ToolSpec]       # [] p/ langchain; specs p/ claude
    tool_dispatch: ToolDispatch # efeitos colaterais FORA do engine
    agent_config; max_tool_iterations=5; enable_prompt_cache=True

@dataclass
class AgentTurn:
    text: str; tool_calls: list[ToolCall]; events: dict  # {qualified_data, qualification_summary, escalate, escalate_reason}
    usage: dict; stop_reason: str | None

class AgentEngine(Protocol):
    engine_name: str
    async def run(self, ctx: AgentContext) -> AgentTurn: ...
```
Seleção por `AIAgent.agent_engine` (`'langchain'` default | `'claude'`), reusa `_engine_cache` de `AIService` (`ai_service.py:389-431`).

### 3.2 `LangChainAgentEngine` (paridade por construção)
Embrulha exatamente o caminho atual: monta `[System, *hist, Human]` (idêntico a `ai_service.py:297-301`), chama `self.llm.ainvoke` (a única linha movida, `:305`), retorna `AgentTurn(text, usage, tool_calls=[], events={})`. Comportamento byte-idêntico. Extração `[QUALIFIED_DATA]` e heurística de escalação **continuam no pipeline** quando `engine=langchain`.

### 3.3 `ClaudeAgentEngine` (tool-use) — a virada de MARCADOR para FERRAMENTAS
Hoje qualificação é marcador `[QUALIFIED_DATA]{...}` (regex `qualification_engine.py:60`) e escalação é heurística **morta** (`result["escalate"]` setado em `ai_service.py:377`, ninguém lê; `build_handoff_context` `guardrails.py:138` nunca chamado). No Agent SDK viram ferramentas chamadas no loop.

```python
async def run(self, ctx):
    system = [{"type":"text","text":ctx.system_prompt,"cache_control":{"type":"ephemeral"}}]  # prompt caching
    tools  = [{"name":s.name,"description":s.description,"input_schema":s.input_schema} for s in ctx.tools]
    messages = ctx.history + [{"role":"user","content":ctx.incoming_text}]
    for _ in range(ctx.max_tool_iterations):                 # cap anti-loop/custo
        resp = await self.client.messages.create(
            model=self.model, max_tokens=1024, thinking={"type":"disabled"},  # SDR conversacional
            system=system, tools=tools, messages=messages)
        _accumulate_usage(usage_acc, resp.usage)
        if resp.stop_reason != "tool_use": break
        messages.append({"role":"assistant","content":resp.content})
        results = []
        for block in resp.content:
            if block.type == "tool_use":
                res = await ctx.tool_dispatch(block.name, block.input, ctx)   # efeito FORA do engine
                results.append({"type":"tool_result","tool_use_id":block.id,"content":json.dumps(res)})
        messages.append({"role":"user","content":results})
    text = "".join(b.text for b in resp.content if b.type=="text")
    return AgentTurn(text=text, tool_calls=..., events=_derive_events(tool_calls), usage=usage_acc, ...)
```

**Fatos de API verificados:** `thinking` rejeitado (400) em modelos 4.6+ (use `adaptive`); `temperature` rejeitado em Opus/Sonnet, aceito em Haiku. **Modelo:** `claude-haiku-4-5` ($1/$5) para o loop + caching; `claude-sonnet-5` só p/ resumo. **Cache mín. prefixo Haiku = 4096 tokens**; prompt IA Mestre ~400–950 tokens pode não ativar → somar `ToolSpec` ao prefixo e medir `cache_creation_input_tokens`.

**Ferramentas de paridade** (`services/agent_engine/tools/`): `register_qualified_lead` (reusa completude do `qualification_engine` + `handle_qualification` idempotente `:48-56`; devolve `{complete, missing}` p/ o modelo emitir o encaminhamento como `prompt_builder.py:117-122`); `escalate_to_human` (materializa `build_handoff_context`, hoje morto). **Fase 2 (dependem do CRMProvider/WS7):** `lookup_crm_contact`, `update_crm_field`, `schedule_meeting`, `get_knowledge`.

### 3.4 Reconexão do miolo (`generate_response` refatorado)
Passos que ficam **no pipeline** (inalterados no caminho langchain): guarda inativo/sem-llm (`:257`); `is_already_qualified_sync` (`:262`); transcrição áudio (`:273`) → `ctx.incoming_text`; escalação heurística (`:276`, só langchain — no claude vira tool); memória 20 msgs (`:282`) → `ctx.history`; `build_system_prompt` (`:290`) → `ctx.system_prompt`; guardrails pós (`:314`) sobre `turn.text`; usage log (`:317`, `service='anthropic'|'openrouter'`); extração qualif (`:322`, regex no langchain / `turn.events` no claude); persistência memória (`:341`, só user+texto final, **não** o transcript de tools); decisão TTS (`:359`); montagem do result (`:363`). Só o passo 7 (montagem messages + `ainvoke`) entra no engine. `AIService._get_agent_for_tenant_sync` e `process_incoming_message` **intactos**; receivers não mudam.

### 3.7 Config por agente/canal (`ai_agents`, `models.py:65`)
`agent_engine` (default `'langchain'`), `anthropic_model` (não cravar; `model` `:79` é formato OpenRouter), `anthropic_api_key` (**cifrado**), `tools_enabled` (JSON, fase 2). + dep `anthropic` no `requirements.txt`.

## 3.5 Fluxo end-to-end (tenant waha+claude)
1. Lead manda áudio → WAHA entrega `POST /webhook/whatsapp/{loc}` (`event:"message"`, `payload:{id, from:"..@c.us", fromMe:false, media:{url,mimetype:"audio/ogg"}}`).
2. `resolve_whatsapp_adapter(tenant)` → `WAHAChannel`.
3. `pipeline.handle_inbound`: `parse_inbound` → `ParsedMessage(sender_id sem @c.us, is_audio=True, from_me=False)`; guards; **dedup** (WAHA re-echoa fromMe); registro GHL se `mode!=whatsapp_only`; `_debounce_schedule`.
4. `_run_debounced`: `ai_service.process_incoming_message(..., channel="whatsapp")` (SEAM).
5. `AIService` resolve agente por (loc,"whatsapp"); `agent_engine='claude'` → `ClaudeAgentEngine`.
6. Pipeline monta `AgentContext` (STT→incoming_text, history 20, system_prompt IA Mestre, tools=[register_qualified_lead, escalate_to_human]) → `turn = await ClaudeAgentEngine.run(ctx)`.
7. Loop Anthropic: system com `cache_control` (2º turno `cache_read>0`); model→`tool_use register_qualified_lead`→dispatch avalia completude→`generate_summary`+`handle_qualification` (opportunity GHL idempotente)→tool_result→model→`end_turn` texto final.
8. Pipeline pós: guardrails sobre `turn.text`; `save_usage_log(service='anthropic', cache_*_tokens)`; **branch estrito**: claude lê `turn.events['qualified_data']` (pula regex); persiste só user+texto final; decisão TTS.
9. Chunking → `WAHAChannel.send_text` por chunk → `_track_sent` + MessageMapping; `send_audio` (data-url → base64 → `/api/sendVoice` PTT).
10. Lead recebe texto+PTT; opportunity criada no GHL.

## 4. Migração strangler — nada é big-bang
**Duas flags independentes:** `Tenant.whatsapp_provider` (borda da rota) e `AIAgent.agent_engine` (`AIEngine.__init__`), default legado + backfill.

**Prova de paridade:** golden/characterization dos dois seams (Passo 0, bloqueante) + contrato por adapter/engine (fixtures Z-API real + 3 engines WAHA → mesmo `ParsedMessage` e mesma sequência downstream). **Claude não é golden-ável** (muda comportamento) → **shadow mode** (roda em paralelo, resposta NÃO enviada, compara taxa de qualificação/escalação/tom por N dias). **WAHA não tem shadow** (sessão real) → **canário num número interno**.

**Rollout: canal primeiro, motor depois.** Canário WAHA = tenant **interno** (NÃO cliente pagante, NÃO a demo Joorney de cara — risco de `@lid` de leads de anúncio), engine NOWEB primeiro, **health-check de sessão STARTED antes de qualquer send**. Critérios: contrato verde; 48h zero disparo duplicado; PTT toca; `@lid` com continuidade; status→GHL ok. Canário Claude = shadow na Joorney demo → flip na própria demo → opt-in por tenant.

**Rollback = 1 linha, blast radius 1 tenant:** `UPDATE tenants SET whatsapp_provider='zapi'` / `UPDATE ai_agents SET agent_engine='langchain'`; Claude cai em LangChain **por exceção dentro do request**. + **kill-switch global** (env/`SystemSettings`) forçando todos p/ `zapi/langchain`. Migrations aditivas nullable → sem down-migration.

⚠️ **Deploy single-worker** (`Dockerfile:29`) → dedup/debounce in-process é seguro hoje. **Bloquear multi-worker + WAHA até ter Redis** (WAHA re-echoa fromMe → callbacks escapam entre workers → resposta duplicada).

## 5. Schema (migrations aditivas)
Migrations vão até **021** (`alembic/versions/`). **⚠️ 022/023 colidem com a alocação da Fase 0** ([[decisoes/produto-saas-fase0]] usa 022=WS2, 023=WS1) — **coordenar a numeração antes de escrever**. Todas: aditivas, nullable, `server_default` no seletor, `batch_alter_table` (dev SQLite).
- **Canal:** `Tenant` + `whatsapp_provider`(default zapi), `waha_base_url/session/engine`, `waha_api_key`(cifrado). Migrar `zapi_token`/`zapi_client_token` (`:20-21`, plaintext hoje) na passada do WS1.
- **Agente:** `AIAgent` + `agent_engine`(default langchain), `anthropic_model`, `anthropic_api_key`(cifrado), `tools_enabled`; `SystemSettings` + `admin_anthropic_key/model`; `UsageLog` + `cache_read_tokens`, `cache_creation_tokens`.

## 6. Encaixe na Fase 0
Os seams + extração do pipeline **sobem para a frente** (bônus: já matam a duplicação do Telegram, item de saúde de código da Fase 0). Convergência: **WS1** cifra as novas creds (cutover amplo é *gated* por WS1; canário interno tolera plaintext); **WS7** — ferramentas de ação usam o `CRMProvider`, mas as de paridade (`register_qualified_lead`/`escalate_to_human`) **não** dependem do WS7; **WS8** — o harness de paridade *é* o WS8 aterrissando cedo.

**Sprints** (1–2 devs após o Passo 0): Sprint 0 golden (bloqueante) → trilha CANAL (extrair pipeline → base+ZAPIChannel+registry+rota → migration 022 → WAHAChannel+service+health-check → canário interno) ‖ trilha AGENTE (extrair seam+LangChainEngine → migration 023+config → ClaudeEngine+ToolRegistry+branch estrito → shadow Joorney → canário). **Esforço ~12–13 semanas-dev**, ~6–7 corridas com 2 trilhas paralelas. Redis e WS1(Fernet) são pré-requisitos externos.

## 7. Primeiro passo concreto
**PR #1 — portas embrulhando o comportamento atual (zero mudança):** (1) golden dos dois seams; (2) extrair `channels/pipeline.py` (relocar dicts debounce/dedup; re-chavear contact_key; apontar cleanup jobs `main.py:94-99`); (3) `channels/base.py`+`ZAPIChannel`+`registry`; (4) rota legada delega + nova `/webhook/whatsapp/{loc}`; (5) `agent_engine/base.py`+`LangChainAgentEngine`. **Aceite:** golden passa sem alteração; sem coluna nova (flags via `getattr` fallback).

**PR #2 — WAHAChannel + ClaudeAgentEngine atrás de flag desligada:** migrations 022/023; `WAHAChannel`+`WAHAService` (contrato 3 engines, health-check); `ClaudeAgentEngine`+`tools` (dep anthropic, caching, cap iterações, fallback automático, branch estrito). **Aceite:** todos em zapi/langchain por backfill → nada muda; deploy single-worker.

## 8. Decisões abertas (default recomendado)
1. **Nº migration** — canal=022, agente=023; **confirmar contra Fase 0** (colidem).
2. **Engine WAHA inicial** — NOWEB; fixture+contrato por engine.
3. **Modelo Anthropic** — `claude-haiku-4-5` + caching; `claude-sonnet-5` só resumo.
4. **Caching efetivo em Haiku** (min 4096 tokens) — somar ToolSpec ao prefixo; medir no canário.
5. **Manter OpenRouter como fallback?** — Sim; não remover até o último agente migrar.
6. **Qualificação tool vs marcador na v1** — marcador no langchain, tool só no claude (branch estrito + idempotência backstop).
7. **Refatorar TelegramChannel agora?** — Deferir para depois do canário WAHA.
8. **Path webhook WhatsApp** — manter `/webhook/zapi/*` legado até o último tenant migrar.
9. **Topologia WAHA** — compartilhado multi-sessão, `session=location_id`.
10. **WAHA emite `message.ack`/status?** — assinar `message.ack`; se não, documentar gap.
11. **Redis antes de multi-worker?** — Sim, obrigatório (single-worker seguro hoje).
12. **Identidade/@lid no WAHA** — migrar tenants não-lead-gen antes; validar `@lid` no canário.
13. **Persistir transcript tool_use/tool_result?** — Não na v1 (só user+texto final).
14. **Destino do handoff de `escalate_to_human`** — definir antes de ligar; default: nota GHL + pausa da IA.
15. **`register_qualified_lead` devolve controle ao modelo?** — Sim (tool_result `{complete,missing}`).
16. **`generate_summary` sob Claude** — chamada one-shot Anthropic dedicada no handler.
17. **`max_tool_iterations` / retry** — cap 5; SDK Anthropic já faz retry 429/5xx (`max_retries=2`).

## Mapa de arquivos
- **Novos:** `channels/{base,registry,pipeline}.py`, `channels/whatsapp/{zapi,waha}.py`, `channels/telegram.py`, `services/waha_service.py`, `webhooks/whatsapp_router.py`, `services/agent_engine/{base,langchain_engine,claude_engine}.py`, `services/agent_engine/tools/`.
- **Extraídos de:** `zapi_receiver.py` (`:239,:119,:30-32,:62-83,:495,:528-567`), `telegram_receiver.py` (`:45,:143,:229`), `ai_service.py` (`:249,:305,:389,:440`).
- **Preservados:** `chat_memory.py`, `master_prompt.py`, `prompt_history.py`, `guardrails.py`, `qualification_handler.py:18`, `qualification_engine.py`.
- **Schema:** `data/models.py` (`Tenant:18, AIAgent:65, MessageMapping:180, SystemSettings:231, UsageLog:195`), `alembic/versions/022_*`, `023_*`.
- **Wiring:** `main.py` (`:86,:94-99,:104-114,:152-161`), `requirements.txt` (+anthropic), `Dockerfile:29` (single-worker até Redis).

## Estado de implementação (branch `feat/pr1-abstracoes-portas`)

PR #1 em andamento — abstrações-primeiro, cada passo com golden verde:

- ✅ **Porta `AgentEngine`** (`services/agent_engine/`) + `LangChainAgentEngine` (paridade). `AIEngine.generate_response` chama `self.engine.run(ctx)`; seleção por `getattr(agent, "agent_engine", "langchain")` (sem coluna). Caracterizado em `tests/test_agent_engine.py`. Commit `2091bf2`.
- ✅ **Contratos `ChannelAdapter`** (`channels/base.py`) + **`ZAPIChannel.parse_inbound`** (`channels/whatsapp/zapi.py`) — normalização do inbound Z-API extraída verbatim; `process_inbound_message` consome `ParsedMessage`. 17 golden tests em `tests/test_channel_zapi.py`. Commit `af37549`.
- ⏳ **Pendente no PR #1:** métodos de envio do `ZAPIChannel` (wrap `zapi_service`), pipeline compartilhado `channels/pipeline.py` (mover `_run_ai_response`/debounce/dedup, separar o mirror-GHL do send), rota universal `POST /webhook/whatsapp/{location_id}`, delegação da rota legada e wiring em `main.py`. É a fatia mais crítica (caminho de envio/debounce) — fazer caracterização-primeiro.
- **Nota:** deploy segue single-worker até o Redis (WS3); flag `agent_engine`/`whatsapp_provider` ainda por `getattr` (colunas entram no PR #2, coordenando numeração de migration com a Fase 0).

Suíte: 111 testes verdes (era 90). ⚠️ Ambiente: o `.venv` estava incompleto — `pytest`, `slowapi`, `alembic` foram instalados para rodar a suíte (Python 3.14).

Relacionado: [[sintese/visao-geral]] · [[decisoes/whatsapp-waha]] · [[decisoes/agente-claude-agent-sdk]] · [[decisoes/produto-saas-fase0]]
