---
type: decisao
status: proposta
updated: 2026-08-18
confidence: medium
---

# Plano do painel: a Mestre que melhora de verdade

**PROPOSTA.** Gerado por painel de 3 desenhos independentes + 3 juízes. Nada aqui
foi implementado além do Bloco 0 (ver [[decisoes/medir-antes-de-melhorar]]).

---

# Plano final — a Mestre Testadora que melhora agente SDK de verdade

## Decisão: espinha dorsal e enxertos

**Espinha = ESTRUTURADO (C)**, mas na versão que os três juízes convergiram: *a política de ação é dado, não prosa* e *a saída da melhoria é um patch, não um blob*. Os dois somam a única propriedade que A e B não têm — tornam o defeito nº 1 **corrigível** e a correção **irreversível por descuido**.

**Enxertos que entram:**
- **De A:** o dossiê (artefato renderizado com rótulo `[EDITÁVEL]`/`[SISTEMA]`, JSON literal das tools, `key` reais, limites de runtime), o lint determinístico e a trava pré-commit. Sem isso o patch fica bem tipado e errado.
- **De B:** a suíte de casos roteirizados rodando o `ClaudeAgentEngine` real — **como script de medição e teste de pytest, não como produto**. É o único instrumento que arbitra se qualquer coisa aqui funcionou.

**Descartado, com motivo:**
- **`ToolPolicy` como coluna JSON por agente + migration (C).** Cardinalidade 1. Vira constante em módulo; a coluna nasce no dia em que o tenant nº 2 precisar divergir. Migration que falha derruba o boot (CLAUDE.md) — não pago esse risco por flexibilidade que ninguém exerce este ano.
- **Juiz-LLM cego, `prompt_eval_runs`, lead-persona, placar com polling na UI (B).** ~500 linhas e US$0,80–1,00 por apply para dar precisão decimal a uma amostra de 1 agente. O Goodhart é máximo em N=1.
- **Migration + backfill do bloco de áudio como pré-requisito (A).** O vazamento se resolve genericizando o texto — edição de string, zero schema.
- **`_apply_diffs` (`admin/ai_agent.py:778-822`) mantido vivo.** Ninguém propôs matá-lo; ele faz match flexível por regex e devolve `success: True` com patch meio aplicado. É via de corrupção **ativa hoje**. Morre no Bloco 2.
- **Vocabulário de 11 `Op` (C).** Começa com 5.

---

## BLOCO 0 — hoje/amanhã. Sem LLM novo, sem migration, melhora **todo** agente SDK

Ordem obrigatória: **0.1 antes de tudo**, porque é o que mede os outros.

### 0.1 A sonda — o instrumento (novo)
**Arquivos:** `/Users/luizantonio/Documents/PROJETOS IA/AxenWP/scripts/sonda_agente.py` (novo, ~180 linhas) + `/Users/luizantonio/Documents/PROJETOS IA/AxenWP/tests/roteiros/*.json` (6 roteiros) + `/Users/luizantonio/Documents/PROJETOS IA/AxenWP/tests/test_agente_casos.py` (novo).

**O que faz:** carrega um `AIAgent` real do banco, monta o contexto pelo **mesmo caminho de produção** (`build_system_prompt(..., for_tools=True)` + `build_tool_specs`), roda `ClaudeAgentEngine.run()` com **dispatch seco** contra falas de lead roteirizadas, e imprime uma tabela: caso → tool chamada, turno, argumentos, `stop_reason`, custo. Sem persona-LLM: as falas do lead são texto fixo.

Os 6 roteiros são os que caçam bug conhecido: `escala_pedido_explicito` (deve escalar), `nao_escala_por_preguica` (pergunta respondível pelo `form_data` → não escala), `nao_escala_no_fechamento` (lead entregou tudo → **qualifica**), `nao_qualifica_incompleto` (2 de 5 campos → não chama a tool, não se despede), `nao_inventa` (lead vago → nenhum valor inventado no `input_schema`), `audio_proposta` (`is_audio_input=True`).

O mesmo arquivo de roteiro alimenta `tests/test_agente_casos.py`, que roda com o `FakeClient` de `/Users/luizantonio/Documents/PROJETOS IA/AxenWP/tests/test_claude_engine.py:44` — **custo zero, dentro do pytest, testando o encanamento** (a política chega ao modelo? o dispatch devolve o que deve?). A sonda com Anthropic real é opt-in por env e testa **comportamento**.

**Como se verifica:** roda a sonda contra o agente da Joorney **antes** de qualquer mudança e guarda o CSV. Esse é o baseline. Estimativa de custo: ~4–8 turnos × 6 casos, prefixo cacheado a partir do 2º turno → **~US$ 0,20–0,35 por rodada** (estimativa, não medida).

### 0.2 Política de ação com fonte única + antídoto
**Arquivos:** `/Users/luizantonio/Documents/PROJETOS IA/AxenWP/utils/tool_policy.py` (novo, ~40 linhas), `/Users/luizantonio/Documents/PROJETOS IA/AxenWP/services/agent_engine/tools.py:61-80`, `/Users/luizantonio/Documents/PROJETOS IA/AxenWP/services/prompt_builder.py:136-149` e `:169-175`.

Hoje a política de escalar existe em **duas strings independentes** que o modelo recebe juntas sem hierarquia: a `description` de `_ESCALATE_SPEC` ("fail-closed — prefira escalar a afirmar algo que não verificou", constante global, idêntica para a frota) e o texto de `build_tools_block` (`prompt_builder.py:147-148`). Nenhuma tem contrapeso.

`utils/tool_policy.py` passa a ser a **única** fonte: `ESCALAR_QUANDO`, `NUNCA_ESCALAR_QUANDO`, `QUALIFICAR_QUANDO`. `tools.py` e `prompt_builder.py` renderizam dela. Em `NUNCA_ESCALAR_QUANDO` entra o antídoto que hoje não existe em lugar nenhum:

> *o lead terminou de informar os dados / está pronto para fechar → isso é `register_qualified_lead`, não transferência; dúvida cuja resposta está no seu prompt → responda, não transfira.*

**Como se verifica:** (a) `tests/test_agente_casos.py` assere que o mesmo literal aparece no `system_prompt` e na `description` da tool, e que `prompt_builder` importa de `tool_policy` — se alguém editar só um lado, o teste fica vermelho; (b) sonda antes/depois: **contagem de escalações em `nao_escala_por_preguica` e `nao_escala_no_fechamento` cai de N para 0**. Esse é o número que o dono vê.

### 0.3 Tirar a instrução suicida do gerador
**Arquivo:** `/Users/luizantonio/Documents/PROJETOS IA/AxenWP/utils/master_prompt.py:293-295`.

A seção `## ESCALAÇÃO` que a Mestre é obrigada a escrever lista, literalmente, *"preço customizado, lead qualificado pronto pra fechar"* como gatilho de transferência. No motor legado era output morto; no SDK isso pausa a conversa no instante da receita. E `master_prompt.py:353-357` (modo apply: *"preserve identidade, tom, estrutura"*) preserva a seção para sempre. Sai "lead qualificado pronto pra fechar" e "preço customizado"; entra "peça de desconto/condição fora da alçada".

**Como se verifica:** teste em `/Users/luizantonio/Documents/PROJETOS IA/AxenWP/tests/test_master_prompt.py` que assere a ausência da frase no `MASTER_SYSTEM_PROMPT`; e o prompt do próximo agente gerado não contém a linha (grep no output).

### 0.4 Dispatch honesto
**Arquivo:** `/Users/luizantonio/Documents/PROJETOS IA/AxenWP/services/ai_service.py:485-496`.

`_claude_tool_dispatch` devolve `{"status":"ok","message":"Lead registrado como qualificado."}` **incondicionalmente**. Só depois, em `:406-408`, `_qualification_complete` descarta a qualificação incompleta. O modelo é informado de sucesso, manda a despedida ("já tenho tudo, um especialista te chama"), **e o CRM fica vazio**. Na conversa salva parece um fechamento perfeito — e é isso que a Mestre lê como sucesso.

Muda para: incompleto → `{"status":"incompleto","faltam":[labels dos campos vazios],"instrucao":"pergunte os campos que faltam; não se despeça"}`. Como o `stop_reason` foi `tool_use`, o loop de `claude_engine.py:110-118` continua no mesmo turno e o modelo corrige sozinho.

**Como se verifica:** (a) teste unitário do dispatch; (b) caso `nao_qualifica_incompleto` na sonda — hoje o modelo se despede, depois não deve; (c) em produção, o log novo de 0.6 dá a série temporal.

### 0.5 A `description` autoral do campo chega ao modelo
**Arquivo:** `/Users/luizantonio/Documents/PROJETOS IA/AxenWP/services/agent_engine/tools.py:40-43`.

`_qualify_spec` monta a propriedade com `"description": f.get("label")` e joga fora `f["description"]` — que a Mestre escreveu (`utils/agent_spec.py:QualFieldSpec.description`) e que `_com_chaves` preservou (`/Users/luizantonio/Documents/PROJETOS IA/AxenWP/services/agent_provisioning.py:100-103`). Uma linha: `f.get("description") or f.get("label") or key`.

**Como se verifica:** teste asserindo que a `description` do campo aparece no `input_schema`. **Não prometo ganho comportamental mensurável** com N=1 — é trabalho já pago sendo descartado, e o teste garante que não seja de novo.

### 0.6 Um log por chamada de tool (a telemetria que falta)
**Arquivo:** `/Users/luizantonio/Documents/PROJETOS IA/AxenWP/services/ai_service.py`, dentro de `_claude_tool_dispatch`.

Hoje as ações do agente são invisíveis: `chat_histories` guarda só user + texto final (invariante de `claude_engine.py:14-17`), e não existe uma linha grep-ável por chamada. Três linhas: `[TOOLS] session=%s tool=%s aceito=%s faltam=%s` — **chaves, nunca valores** (são dados do lead).

**Como se verifica:** é o que torna verificável tudo o mais. Métricas semanais por grep: escalações/semana, qualificações aceitas, qualificações rejeitadas por incompletude, razão escalação÷conversa. Sem isso, toda afirmação de "melhorou" neste plano vira fé.

### 0.7 Trava pré-commit no Aplicar
**Arquivo:** `/Users/luizantonio/Documents/PROJETOS IA/AxenWP/admin/ai_agent.py:1322-1335`.

Hoje: `agent.prompt = output; db.commit()` — sem nenhuma verificação, e o `snapshot_prompt` roda **depois** do commit (não existe ponto de retorno pré-apply). O prompt da Joorney tem ~22k caracteres (comentário em `/Users/luizantonio/Documents/PROJETOS IA/AxenWP/services/draft_service.py:175`) contra `max_tokens: 6000` em `:1313`: **um clique pode salvar em produção um prompt cortado no meio**. Entra, ~30 linhas:

1. `snapshot_prompt(..., source="pre_optimize")` **antes** do commit — e se o snapshot falhar, **aborta** (hoje é `try/except` que engole);
2. rejeita `finish_reason == "length"` **e** `len(output) < 0.6 * len(prompt_atual)` **e** output que não termina em pontuação;
3. rejeita preâmbulo ("Aqui está...") e `contains_placeholder` (`/Users/luizantonio/Documents/PROJETOS IA/AxenWP/utils/guardrails.py`) — regra que a própria Mestre lista como inaceitável no item 9;
4. rejeita `[QUALIFIED_DATA]` quando `agent.agent_engine == "claude"`;
5. aceita chave de qualquer master engine — hoje `:1253-1254` exige `admin_openrouter_key` **para diagnosticar um agente Anthropic**.

**Como se verifica:** teste que injeta resposta truncada e assere que o prompt **não** foi gravado e que existe a linha `pre_optimize` no histórico. E o `history_used` para de mentir (busca 40 em `:1272`, formata 30 em `master_prompt.py:396`, reporta 40 em `:1342`) — teste de igualdade.

**Resultado do Bloco 0:** ~1 a 1,5 dia, zero migration, zero custo por clique, e o dono vê um número: a tabela da sonda antes × depois.

---

## BLOCO 1 — semana 1: os olhos da Mestre

### 1.1 Dossiê
**Arquivo:** `/Users/luizantonio/Documents/PROJETOS IA/AxenWP/services/agent_dossier.py` (novo, ~200 linhas, zero LLM), consumido em `/Users/luizantonio/Documents/PROJETOS IA/AxenWP/admin/ai_agent.py:1292-1298`.

Renderiza, com `build_system_prompt` e `build_tool_specs` **reais**: o prompt final marcado `[EDITÁVEL]` vs `[SISTEMA — anexado depois do seu texto]`; o JSON literal das tools + uma linha nossa por tool com o **efeito** (escalar pausa a IA de forma durável via `/Users/luizantonio/Documents/PROJETOS IA/AxenWP/services/escalation_handler.py:33-36` + o gate de `ai_service.py:314-322`); a tabela `key/label/description/auto`; e os limites (`max_tokens=1024`, `max_tool_iterations=5`, `strip_emojis` sempre, guardrail de regeneração inexistente sem chave OpenRouter em `ai_service.py:198`).

O endpoint já carrega o `agent` inteiro em `:1241-1244` e usa **dois campos**. E `master_prompt.py:163` imprime hoje **"Nenhuma definida"** para as perguntas qualificatórias nos agentes do caminho AgentSpec (`admin/ai_agent.py:1586` seta `""`) enquanto a tool exige 5 campos obrigatórios — a Mestre melhora um agente que ela acredita não qualificar ninguém.

**Como se verifica:** teste que assere que o dossiê contém as `key` reais e que o prompt renderizado é **byte-a-byte igual** ao que `ai_service` monta para o mesmo agente (senão o dossiê vira ficção no primeiro refactor). Ganho: rodar a sonda no prompt que sai do apply **com** e **sem** dossiê e comparar as tabelas. A sonda é o árbitro.

### 1.2 Lint determinístico
Seis regras em `agent_dossier.py`, custo zero: `qualification_questions` vazio com campos preenchidos; `[QUALIFIED_DATA]` em agente claude; nome de dado no texto sem `key` correspondente; gatilho comercial na seção de escalação; `contains_placeholder`; prompt > 1200 palavras. Entra no diagnóstico como pauta antes de gastar token.
**Verificação:** teste com agente sintético para cada regra.

### 1.3 `IMPROVE_SYSTEM_PROMPT` que sabe que existem ferramentas
**Arquivo:** `/Users/luizantonio/Documents/PROJETOS IA/AxenWP/utils/master_prompt.py:327-369`. As 280 palavras atuais não contêm "ferramenta", "tool" nem "qualificação"; os 10 itens do checklist são 100% prosa. Inverte: 6 itens de **ação** no topo (escalação indevida; efeito descrito em prosa sem mandar chamar a tool; formato de saída competindo com a chamada; nomes que não batem com as `key`; instrução de deduzir campo — que contradiz o contrato da tool; resto do motor antigo), e os 10 atuais descem. Mais a regra de não-duplicação: *o bloco `[SISTEMA]` é anexado depois do seu texto; não reescreva a lista de campos*.
**Verificação:** sonda antes/depois do prompt produzido.

### 1.4 Evidência rotulada por desfecho
**Arquivo:** `/Users/luizantonio/Documents/PROJETOS IA/AxenWP/services/evidence.py` (novo, ~120 linhas), substituindo `admin/ai_agent.py:1263-1284`.

Hoje a query pega as 40 últimas linhas de `chat_histories` do **location**, sem canal, sem agente, **intercaladas por tempo entre leads diferentes**, e `_format_conversation` as numera como se fossem um diálogo só. Pior: conversa qualificada ou escalada **para de gerar mensagens** (gate de `ai_service.py:314-322`), então a janela é dominada por casos em aberto — a Mestre aprende quase só com fracasso silencioso e escreve *"o agente abandona o lead, nunca encerre a conversa"*, a instrução oposta à necessária.

Troca por `messages` (`/Users/luizantonio/Documents/PROJETOS IA/AxenWP/data/models.py:336-388`, tem `channel`, `contact_ref`, `sender_role`, índice `ix_messages_thread` pronto) agrupado por thread, janela de 30 dias, join com `qualified_leads` para o rótulo (`_handoff:True` = escalado; linha = qualificado; sem linha = em aberto). E `:1284` para de descartar a sessão do simulador que o operador acabou de rodar por causa de uma linha de seis meses atrás.

**Como se verifica:** teste com banco sintético (3 threads, 3 desfechos) asserindo agrupamento e rótulo; e a resposta declara `fonte: "messages" | "chat_histories"` — o indicador da UI passa a mostrar `4 conversas · 3 escaladas · 0 qualificadas · 30 dias · WhatsApp` em vez de "40 mensagens reais".

---

## BLOCO 2 — semana 2: a saída vira patch

**Arquivos:** `/Users/luizantonio/Documents/PROJETOS IA/AxenWP/utils/agent_spec.py` (+`Op`, +`AgentSpecPatch`), `/Users/luizantonio/Documents/PROJETOS IA/AxenWP/services/spec_patch.py` (novo), `/Users/luizantonio/Documents/PROJETOS IA/AxenWP/services/master_improve.py` (novo, espelho de `master_engine.py:116-152` com `messages.parse`), `/Users/luizantonio/Documents/PROJETOS IA/AxenWP/admin/ai_agent.py` (endpoint + morte do `_apply_diffs`), `/Users/luizantonio/Documents/PROJETOS IA/AxenWP/web/static/js/dashboard.js:3101-3153`.

Cinco ops, não onze: `prompt.substituir`, `prompt.remover`, `campo.adicionar`, `campo.remover`, `politica.nunca_escalar_add`. Cada op carrega `motivo`, `evidencia`, `risco`. `ops: []` é resposta válida — hoje o tipo de retorno não admite "está bom, não mude" e todo apply é reescrita compulsória do que está atendendo.

Três propriedades que só a estrutura dá: **(a)** o output não reemite o prompt → o truncamento de 22k contra 6000 tokens deixa de existir; **(b)** op que não casa exatamente é **rejeitada e reportada** ("aplicadas 2 de 4; 2 rejeitadas porque o prompt mudou") — nada de match flexível por regex nem `success: True` com patch meio aplicado; **(c)** `campo.*` nunca aceita `key` do LLM: passa por `_com_chaves` e re-executa `build_agent_provisioning` contra o CRM real, herdando o fail-closed que a geração já tem.

**Como se verifica:** `tests/test_spec_patch.py` — op que não casa não aplica nada dela e aparece no relatório; `ops: []` não toca no banco; `campo.adicionar` sem pipeline resolvível volta como pendência, não como sucesso. E a sonda: prompt produzido por patch × prompt produzido por blob, mesma tabela.

**UI:** lista de mudanças em português de negócio, todas marcadas por default (o operador pode desmarcar, não precisa entender tool-use), e o Aplicar responde o que de fato aconteceu.

---

## BLOCO 3 — adiado, com gatilho explícito

Banca como produto (endpoints `/eval/*`, `prompt_eval_runs`, juiz-LLM cego, persona viva, placar com polling, selo no card) **só quando existir o tenant nº 3**. Gatilho concreto: mais de um agente cujo prompt diverge, ou mais de 4 applies/mês. Até lá, a sonda como script + os 6 casos no pytest entregam o mesmo sinal por ~US$0,30 e 0 linhas de UI.

---

## O que pode dar errado em silêncio

1. **Áudio genérico enfraquece a Joorney.** Tirar "Parcelow / valor com desconto / rush" de `/Users/luizantonio/Documents/PROJETOS IA/AxenWP/services/prompt_builder.py:20-24` para o texto genérico ("use a sequência definida no seu prompt principal") preserva o comportamento **se** o prompt dela tiver essa seção. Se não tiver, a proposta em áudio degrada sem erro, sem log, só com menos conversão. Mitigação: caso `audio_proposta` na sonda rodado **com o prompt dela** antes de mergear.
2. **O dispatch honesto pode virar loop.** Modelo insiste em chamar QUALIFY incompleto → queima as 5 iterações → `claude_engine.py:120-124` devolve `text=""` → `ai_service.py:431` **grava mensagem vazia no histórico** e o lead recebe silêncio. Já é possível hoje; a mudança aumenta a chance. Mitigação obrigatória junto: na 2ª chamada incompleta do mesmo turno, o dispatch devolve instrução terminal; e `text == ""` não persiste mensagem.
3. **A fonte única volta a divergir.** Alguém edita a `description` da tool sem tocar `tool_policy`. Só o teste de contenção pega — e ele é a única defesa.
4. **Cache invalidado.** Mudar tools/política/prompt muda o prefixo com `cache_control` (`claude_engine.py:46-49`): um miss por deploy e um por apply. Não é dano, é custo que hoje ninguém contabiliza.
5. **Fallback silencioso da evidência.** Se `messages` estiver vazio no tenant, o dossiê cai em `chat_histories` e volta o corpus embaralhado. Por isso a resposta **declara a fonte** — se a UI não mostrar, o operador lê anedota como dado.
6. **Rejeição parcial lida como sucesso.** "Aplicadas 2 de 4" pode deixar o prompt num estado que nem o modelo nem o operador projetaram (a op 2 podia pressupor a op 4). A/B: aplicar tudo-ou-nada por default e só liberar parcial com aviso explícito.
7. **`finish_reason` não confiável.** Se o provider não devolver, a trava 0.7-2 passa batido — por isso ela tem **três** critérios (finish_reason, razão de tamanho, pontuação final), não um.
8. **Snapshot que falha silenciosamente.** Hoje é `try/except` com warning; se continuar assim, o ponto de rollback some sem ninguém saber. Tem que abortar.
9. **Sonda plugada no dispatch real** criaria oportunidade no GHL e pausaria conversa de verdade. Defesa dura: `scripts/sonda_agente.py` não importa `qualification_handler`/`escalation_handler`, telefone com prefixo reservado `_sonda_`, e um teste que assere a ausência do import.
10. **Dossiê maior dilui o diagnóstico.** 6k de input pode gerar texto mais genérico que 2k. Só a sonda diz; se piorar, o bloco sai (é aditivo e reversível).

---

## O que eu não consegui verificar

- **Volume real.** Não tenho acesso ao Postgres de produção. Confirmei no código que `messages` **é escrita** (`services/inbound_pipeline.py`, `webhooks/ghl_provider.py`, `webhooks/zapi_receiver.py`, `webhooks/waha_receiver.py`), mas **não sei quantas threads existem**. Todo o valor do Bloco 1.4 depende disso — se forem 4 conversas, é anedota rotulada, melhor que anedota embaralhada, e nada mais.
- **Que o agente de fato escala demais hoje.** É registro de wiki, não medição minha. Eu não rodei o modelo. A sonda (0.1) existe precisamente para transformar essa premissa em número **antes** de mexer em qualquer coisa — se o baseline mostrar 0 escalações indevidas, o Bloco 0.2 perde a justificativa principal e vira higiene.
- **Os 22k caracteres da Joorney** vêm de um comentário em `services/draft_service.py:175`, não de query. Se o número for menor, o risco de truncamento cai mas não some (6000 tokens ≈ 20k chars em português).
- **Custos.** Todos estimados por contagem de token, nenhum medido em fatura.
- **O comportamento do modelo depois do dispatch honesto.** Que ele volte a perguntar em vez de se despedir é a expectativa; a sonda mede, eu não medi.