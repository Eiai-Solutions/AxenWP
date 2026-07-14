---
type: decisao
status: solid
updated: 2026-07-14
sources: [services/ai_service.py, services/qualification_engine.py, utils/guardrails.py, utils/master_prompt.py]
confidence: high
---

# Decisão: trocar LangChain single-turn por Claude Agent SDK (tool-use)

## Contexto
O motor de agente hoje é **LangChain + OpenRouter single-turn** (`services/ai_service.py`, a chamada LLM é uma linha: `:305` `await self.llm.ainvoke(...)`). O assessment apontou isso como o gap de "plataforma de agentes": sem tool-calling, sem multi-step — o agente só responde, não age.

## Decisão
Adotar o padrão **Claude/Anthropic Agent SDK** = loop de **tool-use** (model → tool_use → tool_result → model), modelos Anthropic, prompt caching. Atrás de uma fronteira `AgentEngine` (ver [[decisoes/reestruturacao-abstracoes-primeiro]]). `LangChainAgentEngine` (paridade) e `ClaudeAgentEngine` coexistem, cutover por agente/canal via flag `AIAgent.agent_engine`.

## A virada de paradigma (o ponto central)
Hoje **qualificação** é marcador de texto `[QUALIFIED_DATA]{...}` extraído por regex (`services/qualification_engine.py:60`) e **escalação** é heurística (`utils/guardrails.py:126`) que **nem é consumida** — `result["escalate"]` é setado em `ai_service.py:377` mas nenhum receiver lê (output morto), e `build_handoff_context` (`guardrails.py:138`) nunca é chamado.

No Agent SDK isso vira **ferramentas** que o modelo chama no loop:
- Paridade (não dependem de outros WS): `register_qualified_lead(fields)` (reusa `qualification_handler.handle_qualification`, idempotente) e `escalate_to_human(reason)` (materializa o handoff hoje morto).
- Fase 2 (dependem do `CRMProvider`/WS7): `lookup_crm_contact`, `update_crm_field`, `schedule_meeting`, `get_knowledge` — o agente que **age**.

## Preservado
IA Mestre v2 (system prompt), memória 20 msgs (`chat_memory`), guardrails (strip_emojis/placeholder/forbidden), versionamento de prompt, decisão de TTS. Tudo fica **no pipeline**; só a chamada LLM entra no engine.

## Modelo e API (verificado no lineup atual)
- **Recomendado:** `claude-haiku-4-5` ($1/$5 por 1M) para o loop de alto volume + **prompt caching**; `claude-sonnet-5` só para resumo/raciocínio complexo. `claude-opus-4-8` fora do loop (custo).
- **Caveats de API:** `thinking` é rejeitado (400) em modelos 4.6+ (use `adaptive` se quiser). `temperature` é rejeitado em Opus/Sonnet, aceito em Haiku. Cache mínimo de prefixo em Haiku = **4096 tokens** — o prompt da IA Mestre (~400–950 tokens) pode ficar abaixo e o cache **não ativar**; mitigar somando `ToolSpec` ao prefixo e medindo `cache_creation_input_tokens` no canário.

## Consequências / decisões abertas
- Sai do OpenRouter (multi-provider) → Anthropic. **Manter OpenRouter como fallback** (LangChainEngine default; Claude opt-in por agente; não remover até o último migrar).
- Nova dep `anthropic` no `requirements.txt` (hoje ausente).
- Config nova (cifrada): `AIAgent.agent_engine`, `anthropic_model`, `anthropic_api_key`, `tools_enabled`.
- Destino do handoff de `escalate_to_human` (nota GHL? número operador?) — definir antes de ligar a flag.
- Prova de comportamento por **shadow mode** (não é golden-ável).

Relacionado: [[sintese/visao-geral]] · [[decisoes/reestruturacao-abstracoes-primeiro]] · [[decisoes/produto-saas-fase0]]
