---
type: decisao
status: solid
updated: 2026-08-16
sources: [services/ai_service.py, services/agent_engine/claude_engine.py, services/agent_engine/tools.py, services/escalation_handler.py, services/qualification_engine.py, data/models.py, services/agent_provisioning.py, alembic/versions/032_motor_padrao_claude.py, tests/test_motor_padrao_sdk.py]
confidence: high
---

# Decisão: trocar LangChain single-turn por Claude Agent SDK (tool-use)

## Estado: MIGRADO (2026-08-16)

**Os 6 agentes rodam no motor `claude`.** O default da coluna virou `claude`
(migration 032) — agente novo nasce no SDK. O legado `langchain` segue no código como
opção, sem nenhum agente usando.

A migração foi feita na ordem que a torna reversível: **o motor foi provado ANTES** de
virar a chave nos agentes que atendem lead. Prova em produção, nos três casos que
importam:

| Caso | Resultado |
|---|---|
| Prompt **placeholder** (Eiai, 40 chars) | **Escalou para humano na 1ª mensagem** |
| Prompt **rico** (Joorney, 22k chars, testado sem tocar no agente dela) | Entrou em persona, respondeu preço, devolveu pergunta de qualificação |
| **Qualificação ligada** (Kozan) | Expôs `escalate_to_human` + `register_qualified_lead`, conduziu a coleta sem disparar tool cedo demais |

### O efeito que só existe no SDK (o achado)

**Prompt fraco + ferramentas disponíveis faz o agente escalar tudo.** Não é falha do
motor — é o que um agente sem nada a dizer faz quando tem um botão de "chamar humano".
Com o motor legado (single-turn, sem tools) o mesmo prompt fraco daria uma resposta
genérica em vez de escalar.

Consequência prática: **migrar agente mal configurado muda o comportamento dele de forma
visível para o lead.** Antes de migrar/criar agente novo, o prompt precisa ter substância
— é exatamente o que [[decisoes/entrevista-da-mestre]] existe para garantir.

### Prompt caching: medido, funciona

`cache_write=10757` no 1º turno e `cache_read=10757` no 2º, com o prompt real da Joorney.
Fecha a pendência "medir `cache_read>0`" que esta página listava.

### Onde o motor é decidido

Cinco lugares faziam agente NOVO nascer no legado; todos virados:
`data/models.py` (default da coluna), `admin/ai_agent.py` (default do Form),
`web/static/js/dashboard.js` (fallback do modal), e `services/agent_provisioning.py`,
que agora define `"agent_engine": "claude"` **explicitamente** — as tools de qualificar
e escalar são justamente o que o legado não sabe usar. `tests/test_motor_padrao_sdk.py`
trava os quatro.

- **PR1** (`6786798`): `services/agent_engine/claude_engine.py` (o loop model↔tools + caching +
  invariante tool_use/tool_result), `tools.py` (specs), migration 027 (`agent_engine`,
  `anthropic_model`, `anthropic_api_key`, `admin_anthropic_key`), dep `anthropic`.
- **PR2** (`c18ade7`): fiação em `ai_service` (constrói o engine + deriva ações de `turn.tool_calls`),
  `escalation_handler.py`, `ghl_service.create_contact_note`, `prompt_builder.build_tools_block`,
  `pipeline` consome `handoff`. 4 achados adversariais corrigidos antes do deploy (gate legado,
  guard de completude, kill-switch durável nos dois modos).
- **Falta (PR3+):** fail-closed 2.3 nas tools · taxonomia de erro 3.3 · reordenar `_AUDIO_MODE_BLOCK`
  para não quebrar o prefixo do cache. (~~ligar na Eiai e medir `cache_read>0`~~ — feito
  em 2026-08-16, ver acima.)

## Decisões travadas com o dono (2026-07-22)
- **Anthropic direto** (não OpenRouter) para o motor claude — o caching real (~87%) depende do
  `cache_control` da API Anthropic. OpenRouter segue como o motor langchain legado.
- **Escalar = pausar IA + nota no CRM** (kill-switch). ghl: custom field "Status IA"; whatsapp_only:
  linha em `QualifiedLead` (o gate desse modo), idempotente.
- **Sonnet default** (`claude-sonnet-5`), sobrescrevível por agente em `anthropic_model`.

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
- **Recomendado (2026-07-22):** `claude-haiku-4-5` ($1/$5 por 1M) para o loop de alto volume + **prompt caching**; `claude-sonnet-5` só para resumo/raciocínio complexo.
  > ⚠️ **Divergência não resolvida:** a migração de 2026-08-16 subiu com `claude-sonnet-5`
  > como default (`claude_engine.py:27`) para os 6 agentes, contrariando esta recomendação.
  > Ninguém revisitou a conta de custo. Ver [[sintese/contradicoes]].
- `claude-opus-4-8` era o id no seletor do painel e estava **desatualizado**; corrigido para
  `claude-opus-5` em 2026-08-16 (nos dois seletores: agente e IA Mestre).
- **Caveats de API:** `thinking` é rejeitado (400) em modelos 4.6+ (use `adaptive` se quiser). `temperature` é rejeitado em Opus/Sonnet, aceito em Haiku. Cache mínimo de prefixo em Haiku = **4096 tokens** — o prompt da IA Mestre (~400–950 tokens) pode ficar abaixo e o cache **não ativar**; mitigar somando `ToolSpec` ao prefixo e medindo `cache_creation_input_tokens` no canário.

## Consequências / decisões abertas
- Sai do OpenRouter (multi-provider) → Anthropic. **Manter OpenRouter como fallback** (LangChainEngine default; Claude opt-in por agente; não remover até o último migrar).
- Nova dep `anthropic` no `requirements.txt` (hoje ausente).
- Config nova (cifrada): `AIAgent.agent_engine`, `anthropic_model`, `anthropic_api_key`, `tools_enabled`.
- Destino do handoff de `escalate_to_human` (nota GHL? número operador?) — definir antes de ligar a flag.
- Prova de comportamento por **shadow mode** (não é golden-ável).

Relacionado: [[sintese/visao-geral]] · [[decisoes/reestruturacao-abstracoes-primeiro]] · [[decisoes/produto-saas-fase0]] · [[decisoes/ia-mestre-portadora-do-metodo]] (quem **gera** as configs que rodam neste motor) · [[decisoes/entrevista-da-mestre]] (a porta que produz prompt com substância — pré-requisito deste motor não escalar tudo) · [[sintese/contradicoes]]
