---
type: conceito
status: solid
updated: 2026-07-14
sources: [main.py, data/models.py, services/ai_service.py, webhooks/zapi_receiver.py]
confidence: high
---

# Visão geral — o que é o AxenWP e para onde vai

## O que é hoje
Hub de integração **FastAPI multi-tenant** que conecta o CRM **GoHighLevel (GHL)** ao **WhatsApp (via Z-API)** e ao **Telegram**, com **agentes de IA SDR por canal**, **qualificação automática de leads** e **geração de prompts via IA Mestre**. Async-first, escopado por `location_id` (`data/models.py:7`).

**Ponte bidirecional:** mensagem no WhatsApp → conversa no GHL; mensagem do agente humano no GHL → sai no WhatsApp; status sincroniza de volta. **Agente SDR:** LLM (via OpenRouter, single-turn — `services/ai_service.py:305`) conversa, transcreve/responde áudio, e ao coletar os dados de qualificação cria oportunidade no funil do GHL e silencia para o humano assumir.

**Modos de tenant** (`Tenant.mode`, `data/models.py:34`): `ghl` (CRM completo OAuth/PIT), `whatsapp_only` (Z-API sem CRM), `lite` (só onboarding). Essa é a espinha dorsal para os dois modos-alvo do produto.

**Fundação boa a preservar:** multi-tenancy por `location_id`; `session_id = f"{location_id}_{phone}"` (`services/chat_memory.py:18`); IA Mestre v2 (`utils/master_prompt.py`); versionamento de prompt (`services/prompt_history.py`); onboarding desacoplado (`public/onboarding.py`); memória 20 msgs; guardrails (`utils/guardrails.py`); migrations idempotentes.

## Para onde vai
Virar uma **plataforma SaaS comercial** de agentes de IA + WhatsApp não-oficial, em **dois modos** — acoplado ao CRM Axen (white-label do GHL) e standalone — e **expansível a outros canais** (Instagram, Messenger, SMS, webchat). Ver [[decisoes/produto-saas-fase0]].

Três mudanças estruturais em curso, todas **atrás de abstrações** (plugue, não rewrite):
1. **Transporte de WhatsApp:** Z-API → **WAHA** self-host, atrás de um `ChannelAdapter`. Ver [[decisoes/whatsapp-waha]].
2. **Motor do agente:** LangChain single-turn → **Claude Agent SDK** (tool-use), atrás de um `AgentEngine`. Ver [[decisoes/agente-claude-agent-sdk]].
3. **CRM:** GHL hardcoded → `CRMProvider` plugável (mais CRMs + painel de chat próprio depois).

O plano que costura tudo: [[decisoes/reestruturacao-abstracoes-primeiro]].

## Riscos estruturais conhecidos (do assessment)
- Webhooks **sem verificação de assinatura** (`utils/config.py:36` declara `zapi_webhook_secret`, nunca usado).
- Segredos de tenant em **texto plano** no banco (`data/models.py:19-28`).
- Estado crítico **in-memory process-local** (dedup/debounce/qualificação) quebra em multi-worker; deploy é single-worker (`Dockerfile:29`).
- **Zero garantia de entrega** (sem retry/circuit/fila).
- Multicanal **copy-paste** (~70% dup entre `zapi_receiver.py` e `telegram_receiver.py`).
