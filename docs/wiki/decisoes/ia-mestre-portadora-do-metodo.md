---
type: decisao
status: solid
updated: 2026-07-22
sources: [utils/master_prompt.py, admin/ai_agent.py, services/agent_engine/tools.py, public/onboarding.py, ~/.claude/skills/criar-agente-sdk]
confidence: high
---

# Decisão: a IA Mestre carrega o MÉTODO de criação de agentes

**Status:** direção travada + **as duas perguntas em aberto resolvidas com medição** (2026-07-22).
**Não implementada.** É o norte
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

## O BLOQUEADOR que reordena tudo (medido 2026-07-22)

`create_agent_from_submission` (`admin/ai_agent.py:1479-1503`) grava **apenas** `name`,
`prompt` e `form_data`. Todas as colunas que fazem o agente **funcionar** ficam no
default do modelo (`data/models.py:123-131`): `is_active=False`,
`qualification_enabled=False`, `qualification_pipeline_id=None`, `stage_id=None`,
`fields=None`, `agent_engine='langchain'`.

**A Mestre preenche 1 de 35 colunas.** Consequência: `tools.py:89-93` só inclui
`register_qualified_lead` quando há `qualification_enabled` + campos — então um agente
gerado hoje nasce **desligado e com uma tool só** (`escalate_to_human`), estruturalmente
incapaz de qualificar. `qualification_handler.py:67` exige `pipeline_id and stage_id`
para criar a opportunity; sem isso, nenhum efeito no CRM.

> **Ligar o gatilho automático hoje produziria agentes mudos.** Este é o pré-requisito —
> não o motor, não as tools. Um Agent Spec estruturado que não alimente essas colunas
> não muda nada.

## Perguntas resolvidas (2026-07-22)

### 1. Motor: Anthropic **single-turn com structured output** — não tool-use

**Migrar** para Anthropic direto, mas com um caller próprio fino (~30 linhas), **sem**
reusar `ClaudeAgentEngine`.

- **O driver é o output contract, não custo.** A API Anthropic tem `json_schema` de
  primeira classe; o OpenRouter repassa com suporte variável por provider — seria
  construir a peça mais crítica sobre uma garantia que não controlamos.
- **Caching NÃO paga aqui** (mata o argumento herdado do motor): prefixo medido =
  6.752 chars (~1.7-2.2k tokens), **abaixo do mínimo de 4096 do Opus** — `cache_control`
  seria silenciosamente inoperante. E o padrão é esparso (1 chamada por onboarding, dias
  de intervalo): break-even exige 21,7% de hit rate. Economia otimista: **~$7,56/ano**.
  Passar `enable_prompt_cache=False` explicitamente.
- **Reusar o `claude_engine` seria reuso negativo:** o loop é genérico
  (`claude_engine.py:77-124`), mas `client.messages.create` (:85-91) passa só 5
  parâmetros — sem `output_config`. Uma tool forçada de saída seria tratada como efeito
  colateral (:101, :115-118): gastaria uma 2ª chamada e enterraria o Spec em
  `ToolCall.result`. Some-se `AgentContext` exigindo `session_id`/`user_phone` dummy e
  `max_tokens` por-instância (1024 vs os 6000 que a Mestre usa).

### 2. Tools: **prefetch determinístico**, não tool-use

A Mestre **precisa** de dado do CRM (`qualification_pipeline_id`, `stage_id`,
`ghl_field_id` são IDs opacos que só existem no CRM do cliente) — mas isso **não** exige
tool-use:

- **Zero graus de liberdade:** `get_pipelines` (`ghl_service.py:372`) e
  `get_custom_fields` (:422) recebem **um** argumento (`location_id`), já conhecido antes
  do modelo rodar. Sempre as duas, sempre primeiro, sempre o mesmo argumento.
- **Sem encadeamento:** `get_pipelines` já devolve os stages embutidos (prova no
  consumidor, `dashboard.js:1346`); `get_custom_fields(model='all')` já concatena tudo.
- **O código de prefetch já está escrito** no mesmo arquivo (`ai_agent.py:374-412`, com a
  guarda fail-closed).
- **Decisivo — fail-closed fica MAIS FRACO com tools:** `if pipelines.get("error"):
  spec.qualification_enabled = False` é invariante de código. Com tools vira *instrução
  de prompt* que o modelo pode desobedecer (ou nem chamar a tool). Trocar invariante por
  promessa do LLM, justamente na propriedade de segurança, é regressão.

> Tools se justificariam se a recuperação fosse **condicional/iterativa** (nenhum pipeline
> casou → tentar outro critério → talvez criar). Não é o escopo.

## Ordem de ataque corrigida

1. ✅ **Ampliar `create_agent_from_submission`** para gravar as colunas de ativação e
   qualificação. **Feito** (`379e675`) — ver seção abaixo.
2. **Prefetch** (2 awaits) + **Agent Spec validado** contra os conjuntos retornados —
   diferença de conjuntos em Python, não julgamento do LLM. *(prefetch já entregue no
   passo 1 via `services/agent_provisioning.fetch_crm_catalog`)*
3. ✅ **Migrar a chamada** para Anthropic single-turn com `json_schema`. **Feito**
   (`a18888c`) — ver seção abaixo. Falta só **validar a qualidade** com API real antes de
   ligar o toggle em produção.
4. **Só então** o gatilho automático do onboarding.

## Passo 3 — entregue (`a18888c`): a Mestre emite AgentSpec

- **`utils/agent_spec.py`** — o contrato (Pydantic `AgentSpec`/`QualFieldSpec`). Por
  **construção** o schema omite `qualification_pipeline_id/stage_id`, `ghl_field_id`,
  `qualification_enabled`, `is_active`, `key` e chaves: o LLM **não tem onde** pôr ID de
  CRM nem flag. Defesa mais forte que instruir o modelo. `QualFieldSpec` = só
  `label`/`description`/`type` (intenção). Revisão adversarial confirmou o schema limpo.
- **`services/master_engine.py`** — caller próprio (não reusa o `ClaudeAgentEngine`, que é
  loop de tool-use e não devolve saída estruturada). `messages.parse(output_format=AgentSpec)`;
  `system` string pura (sem `cache_control`). Fail-closed: qualquer falha levanta →
  submissão fica `pending`.
- **Fusão com o passo 1:** `build_agent_provisioning` ganhou `fields_override` — a fonte dos
  campos passa a ser o Spec; o parser de texto livre vira fallback. `key`/`ghl_field_id`/
  pipeline/stage seguem resolvidos por código contra o CRM; `qualification_enabled = intent
  AND pipeline+stage` (fail-closed preservado).

### Quirks duráveis (custaram tempo)

- **Gate PRÓPRIO da Mestre** (achado da revisão): a mesma `admin_anthropic_key` liga o
  **motor** Claude de um agente. Sem um gate separado, a Mestre de **todos** os tenants
  trocaria de OpenRouter-prosa para AgentSpec no instante em que a chave aparecesse. Por
  isso `is_configured()` exige chave **E** o toggle `MASTER_ENGINE=anthropic` (ou
  `MASTER_USE_SPEC=1`). Sem o toggle → legado byte-idêntico. **Ligar em produção é
  deliberado.**
- **Floor do SDK:** `anthropic>=0.80.0`. Verificado baixando os wheels: **0.69 NÃO tem**
  `messages.parse`/`output_format` (entrou entre 0.75 e 0.80). O `>=0.40` anterior
  quebraria a Mestre estruturada em produção.
- **Shape do `output_config`:** `{"format": {"type": "json_schema", "schema": {...}}}`;
  `messages.parse` aceita `output_format=<Pydantic>` e o SDK gera o schema. `parsed_output`
  é a instância ou `None`.

## Passo 1 — entregue (`379e675`), e o que ele ensinou

`services/agent_provisioning.py` monta a config além do prompt: deriva
`qualification_fields` do texto livre das perguntas, pré-busca o catálogo do CRM
(pipelines + custom fields, determinístico), casa campo→CRM e escolhe pipeline/stage.
Ligado em `create_agent_from_submission` **e** em `save_form_data` (aba Cadastro).

**Fail-closed real:** sem funil definido, a qualificação fica **desligada**. Ligar sem
pipeline/stage seria a pior falha e silenciosa — o agente diria ao lead que registrou, o
handler pularia o CRM sem logar (`qualification_handler.py:67`), e `ai_service.py:319`
**pausaria a IA para sempre** naquele lead (idempotência impede o reenvio mesmo após
correção). Melhor não prometer qualificar do que engolir o lead.

**A verificação adversarial pegou 4 bloqueadores** (corrigidos antes do deploy) — dois
eram erro de premissa: (a) a mudança seria **no-op**, porque o formulário público não
coleta `qualification_questions` e eu testava injetando o campo à mão; (b) meu
"fail-closed" estava **fail-open** (ligava com pipeline vazio).

> **Lição que reorienta o passo 3:** a fonte dos `qualification_fields` **não deve ser um
> parser de texto livre** — deve ser o **Agent Spec da Mestre**, que tem o contexto do
> negócio para decidir o que coletar e qual o tipo de cada campo. O parser fica como
> fallback do que o operador digita à mão. O *encanamento* do passo 1 (escrever config,
> pré-buscar CRM, fail-closed, report auditável, preservar config manual) é o que
> permanece e o que o Spec vai alimentar.

## Achados colaterais (dívidas que apareceram)

- **A "Mestre" são 5 call-sites, e só 2 usam `master_prompt.py`.** `analyze-prompt`
  (`ai_agent.py:758-901`) e `master-chat` (:914-1030) têm prompts **inline**. O método só
  existe em 2 dos 5 lugares — e "uma chave a menos" só fecha se os 5 migrarem.
- **Rota morta:** `save_form_data(regenerate=True)` (:1321) é inalcançável pela UI —
  `dashboard.js:561` fixa `regenerate: false`.
- **Onde o caching REALMENTE pagaria** (e ninguém tinha olhado): `analyze-prompt` reenvia
  o prompt inteiro do agente **3x** (~15k tokens de repetição pura; o `JOORNEY_PROMPT` tem
  20.247 chars) e `master-chat` reenvia a cada turno.
- **`agent_engine` não aparece em `admin/` nem `web/`** — trocar langchain→claude só é
  possível direto no banco.
- **Lacuna de formulário:** o onboarding coleta 14 campos, `build_company_context` lê 19.
  No caminho automático, `agent_type` cai em `inbound` por default (:1455) e o
  `MASTER_SYSTEM_PROMPT` ramifica pesado em INBOUND vs OUTBOUND. É decisão de produto.
- ~~`qualification_questions` (texto livre) nunca vira `qualification_fields`~~ — **resolvido
  no passo 1** (`derive_qualification_fields`), mas ver a lição acima: o Spec da Mestre é a
  fonte definitiva; o parser é o fallback do que o operador digita.

## Sem prova (assumir com cautela)

- Token count real não foi medido com `count_tokens` (usei ~4 chars/tok e cl100k). A
  conclusão é robusta às duas estimativas, mas o número exato não está provado.
- **Qualidade do output na migração**: o `MASTER_SYSTEM_PROMPT` foi tunado contra gpt-4o
  (fallback em :1458). "Migrar melhora" é hipótese até um A/B.
- Como versionar o Spec (`agent_prompt_history` hoje versiona prosa).
