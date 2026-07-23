---
type: decisao
status: draft
updated: 2026-07-22
sources: [utils/master_prompt.py, admin/ai_agent.py, services/agent_engine/tools.py, public/onboarding.py, ~/.claude/skills/criar-agente-sdk]
confidence: medium
---

# Decisão: a IA Mestre carrega o MÉTODO de criação de agentes

**Status:** direção travada com o dono (2026-07-22), **não implementada**. É o norte
da frente "Mestre" — escrito antes de codar justamente para não construí-la errado.

## O problema

A visão de produto é **self-service**: o cliente preenche o formulário e sai um agente
funcionando, sem o Luiz no meio ([[decisoes/produto-saas-fase0]]). Mas cada agente tem
peculiaridades — mesmo com todos os campos mapeados, alguém precisa **traduzir** o
formulário num agente bem construído.

Hoje quem faz essa tradução é a **IA Mestre** (`utils/master_prompt.py`), e ela produz
um **blob de texto** (o prompt de sistema do agente). Ela já tem uma versão leve de
método: detecta o registro de tom (`_detect_register`, `master_prompt.py:53`) e adapta a
estrutura por tipo de atendimento (`MASTER_SYSTEM_PROMPT`, `:174`). O que ela **não**
tem é o método de *arquitetura de agente* — quais tools, quais regras fail-closed,
quais guards. Isso hoje mora fora do produto: na skill `criar-agente-sdk`.

## A decisão

A Mestre passa a **carregar o método** da skill `criar-agente-sdk` — a Fórmula
(objetivo → intake por slots → matriz de padrões → Agent Spec) — e a emitir uma
**config estruturada**, não um blob de prosa.

Assim, todo agente gerado **nasce seguindo as mesmas regras de produção**, por
construção — e não por sorte de o prompt ter saído bom.

## As três camadas (o modelo mental)

1. **Skill `criar-agente-sdk` = metodologia.** Guia (a) o agente de código construindo
   o motor e (b) o método da Mestre. Não roda em produção.
2. **IA Mestre = aplica a metodologia.** Recebe o `form_data` e emite a **config** do
   agente do cliente.
3. **Agentes do cliente = a config rodando no motor** ([[decisoes/agente-claude-agent-sdk]]).

O insight do dono: **a camada do meio tem que carregar a metodologia.** É o que fecha o
laço — a mesma disciplina que constrói o motor constrói os agentes em cima dele.

> Mecânica honesta: a skill é ferramenta do agente **de código**; a Mestre em produção
> não "invoca" a skill. O **conhecimento** dela é destilado no método da Mestre.

## A divisão que evita o erro: DESIGN vs IMPLEMENTAÇÃO

A skill tem duas metades e **só uma vai "dentro" da Mestre**. Ignorar isso levaria a
mandar a Mestre "implementar idempotência" — coisa que ela não pode fazer, porque ela
gera **config**, não código.

| Metade | Conteúdo | Quem faz | Onde vive |
|---|---|---|---|
| **Design** | Fórmula, comportamento como dado, persona, slots, fail-closed, quais claims guardar | **A Mestre**, por cliente | na config gerada |
| **Implementação** | lock, idempotência, saga, caching, taxonomia de erro, o loop de tools | **O dev, uma vez** | no motor (`services/agent_engine/`) |

A Mestre usa a matriz de padrões como **checklist** para produzir uma config completa e
segura; o **motor impõe** o mecanismo. Ela precisa saber *"esse agente qualifica leads →
ativar a tool de qualificação, fail-closed em dado ausente"* — não precisa saber escrever
um lock.

## Como o método entra na Mestre (3 movimentos)

1. **Método** — o system prompt da Mestre passa a *ser* a Fórmula (intake por slots +
   matriz de padrões), evoluindo o `MASTER_SYSTEM_PROMPT` (`master_prompt.py:174`).
2. **Output contract** — a saída vira um **Agent Spec estruturado** (quais tools, quais
   campos, quais regras fail-closed, persona) em vez de só texto. É o que torna a
   geração **auditável e completa**: para cada padrão dá para dizer entra/não-entra.
   Hoje `build_messages` (`master_prompt.py:314`) devolve mensagens que produzem prosa.
3. **Validação** — a Mestre ganha uma checagem de consistência sobre a própria config
   antes de salvar.

## Por que agora e o que destrava

Destrava o **gatilho automático** do onboarding: hoje o formulário público só grava a
submissão como `pending` (`public/onboarding.py:109-112`) e o operador decide. Com a
Mestre confiável, esse passo vira automático.

Sequência: validar o motor claude na Eiai → **evoluir a Mestre** → ligar o gatilho.

## Pontos em aberto

- A Mestre roda hoje em **OpenRouter** (`admin/ai_agent.py:772`), enquanto o motor dos
  agentes foi para **Anthropic direto** ([[decisoes/agente-claude-agent-sdk]]). O dono
  levantou que a Mestre deveria ser "o maior agente SDK" — se ela vira tool-use com
  Anthropic, ou segue single-turn no OpenRouter, **ainda não foi decidido**.
- O catálogo de tools hoje é derivado da config (`services/agent_engine/tools.py:83`,
  só qualificação + handoff). Um Agent Spec mais rico pede um catálogo maior.
- Como versionar o Spec (o `agent_prompt_history` hoje versiona prosa).
