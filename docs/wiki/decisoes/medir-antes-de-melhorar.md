---
type: decisao
status: parcial
updated: 2026-08-18
sources: [utils/master_prompt.py, services/ai_service.py, services/agent_engine/tools.py, services/prompt_builder.py, scripts/sonda_agente.py, tests/roteiros/comportamento_sdr.json, tests/test_agente_casos.py]
confidence: high
---

# Decisão: medir o agente antes de "melhorar" o agente

**Status:** correções no ar; instrumento construído; melhoria da Mestre ainda não feita.

## O pedido

Do Luiz: *"preciso realmente deixar ele [o testador da IA Mestre] extremamente bom,
ele precisa melhorar mesmo os meus agentes sdk's"*.

## O que o painel achou, e que muda a ordem do trabalho

Painel de 3 propostas independentes + 3 juízes concluiu que **antes de melhorar a
Mestre, o produto precisa parar de mandar o agente errar**. Três defeitos, todos
silenciosos, todos verificados por leitura direta:

| # | onde | o que fazia |
|---|---|---|
| 1 | `master_prompt.py:293` | obrigava a Mestre a escrever "transfira quando o lead estiver **pronto pra fechar**" — e no SDK transferir **pausa** a conversa |
| 2 | `ai_service.py:487` | `_claude_tool_dispatch` devolvia `"ok"` **incondicional**; o guard descartava depois, em silêncio. O lead ouvia "um especialista vai te chamar" e **o CRM ficava vazio** |
| 3 | `tools.py:40` | jogava fora a `description` que a Mestre escreveu sobre cada campo |

O nº 1 explica o "escala tudo" que já tínhamos atribuído a prompt fraco: **era
instrução explícita**, e o modo `apply` (que manda "preservar estrutura") a
perpetuaria.

O nº 2 é o pior de operar: a conversa salva **parece um fechamento perfeito** — e é
essa conversa que a Mestre leria como exemplo de sucesso ao melhorar o prompt.

## A sonda, e o resultado que veio contra mim

Não havia como responder *"esse prompt novo é melhor que o velho?"*. `scripts/sonda_agente.py`
roda o `ClaudeAgentEngine` real contra falas roteirizadas e reporta **qual ferramenta
o agente chamou**. Mede AÇÃO, não texto — texto é estilo e muda a cada rodada.

`--historico <id>` mede uma versão antiga do prompt. É assim que se prova uma melhoria.

**E o primeiro uso derrubou minha própria conclusão:** corrigi o prompt do agente de
produção (removendo o gatilho de escalação no fechamento), rodei a sonda — 6/6 — e
rodei também a versão anterior: **6/6 também**. A correção não produziu diferença
mensurável. Continua certa, mas eu teria anunciado uma vitória que não ganhei.

**A lição que fica é sobre a ordem:** construir o instrumento ANTES de comemorar. Sem
ele, três correções plausíveis viram três alegações não verificadas.

**E uma consequência honesta:** com tudo passando antes E depois, o roteiro estabelece
um PISO (o agente não está quebrado nesses seis eixos) mas **não discrimina**. Casos
que separam bom de ótimo precisam sair de conversa real, não da minha imaginação.

## O estado do agente do Luiz

- `qualification_enabled=False` e os 3 campos são `auto` (Nome/Email/Estado, vindos do
  CRM). **Nenhum campo de coleta** → `build_tool_specs` corretamente NÃO oferece
  `register_qualified_lead`, e a única ação que a Ellen conhece é **escalar**.
- O Luiz vai cadastrar os campos. Quando cadastrar, a ferramenta aparece sozinha — e é
  aí que os casos de qualificação do roteiro passam a medir alguma coisa.

## O que continua em aberto

O pedido original — a Mestre melhorar de verdade — **não foi feito**. O plano do painel
está em [[decisoes/mestre-melhoria-plano]]. O essencial: a Mestre roda em OpenRouter
`gpt-4o` com 280 palavras de system prompt que **não mencionam ferramenta nem
qualificação**, enquanto o agente é Anthropic tool-use. Ela otimiza prosa para um
agente cujo comportamento é decidido por *quando chamar qual tool*.

Relacionado: [[decisoes/agente-claude-agent-sdk]] · [[decisoes/ia-mestre-portadora-do-metodo]]
