---
type: decisao
status: proposta
updated: 2026-08-17
sources: [services/inbound_pipeline.py, services/ai_service.py, services/chat_memory.py, services/channel_policy.py, channels/base.py, channels/registry.py, webhooks/ghl_provider.py, webhooks/waha_receiver.py, webhooks/zapi_receiver.py, admin/waha.py, services/draft_service.py, services/agent_wizard.py, data/models.py]
confidence: medium
---

# Plano completo: duas contas do mesmo canal (metade B)

**Status: PROPOSTA.** Nada aqui foi implementado. Gerado por mapeamento com 4 leitores
paralelos sobre o código real — 92 pontos de acoplamento, todos com arquivo:linha.

Leia [[decisoes/multiplos-agentes-por-instancia]] antes: ele explica por que a metade A
foi separada e já entregue, e traz a correção de premissa (produção tem **1** agente,
não os 6 do CLAUDE.md).

Duas ressalvas do próprio plano, que continuam valendo:

- o estado do banco de produção **foi** verificado depois (1 tenant, 1 agente), o que
  torna a migração de dados muito mais barata do que o texto abaixo assume;
- **não foi verificado** se a Z-API envia `instanceId` em todos os tipos de evento
  inbound. Se faltar em algum, a Fase 3.3 precisa de URL discriminada como o Telegram.

---

# Plano faseado — múltiplos agentes por instância (MilloChat)

## 0. A leitura que organiza tudo

O pedido tem **duas metades com custos de ordem de grandeza diferentes**, e elas estão embaralhadas na mesma frase:

| Metade | O que é | Custo | Depende de decisão do dono? |
|---|---|---|---|
| **A — "listar agentes pelo nome, clicar abre aquele agente"** | Trocar a identidade do agente na UI e na API de `channel` para `id`. Os agentes que já coexistem (whatsapp + telegram) passam a aparecer nomeados. | Médio, contido em `admin/ai_agent.py` + `dashboard.js` + `modals.html` | **Não.** Reversível, sem migration de dados. |
| **B — "duas contas do mesmo canal"** | Criar o conceito de CONTA (hoje inexistente: `data/models.py:25-37` guarda uma credencial por canal em colunas planas), fazê-la viajar do webhook até o envio, e reescrever a regra `um provedor por instância` (`services/channel_policy.py:43`). | Grande, atravessa schema, inbound, outbound, memória, CRM e UI | **Sim, 6 decisões.** Algumas irreversíveis. |

**A metade A entrega o que o dono vê. A metade B entrega o que ele pediu.** Elas compartilham exatamente um pré-requisito — trocar `channel` por `id` como identificador — e é por isso que A deve vir primeiro: é o degrau que B precisaria construir de qualquer jeito.

Existe ainda uma **metade zero**, que não é feature nenhuma: sete queries que já estão erradas *hoje*, com whatsapp + telegram na mesma instância.

---

## 1. Decisões que o dono precisa tomar (antes da Fase 2)

Nenhuma dessas é decisão de engenharia. Cada uma **muda o trabalho**, e três delas são difíceis de reverter depois de rodar em produção.

### D1 — Duas contas do mesmo canal podem usar **provedores diferentes**?
(Ex.: número 1 na Z-API, número 2 no WAHA, na mesma instância.)

- **Se NÃO** (uma instância = um provedor de WhatsApp, N números dentro dele): `services/channel_policy.py:43` sobrevive quase intacto — vira `active_whatsapp_provider(tenant)` ainda por instância, e a conta só carrega credencial. Os 13 testes de `tests/test_channel_exclusivity.py` continuam válidos.
- **Se SIM**: `whatsapp_conflict` (`channel_policy.py:62`) e `conflict_message` (`:74`, que literalmente diz *"é um provedor de WhatsApp por instância"*) precisam ser reescritos, e as guardas de inbound (`webhooks/zapi_receiver.py:278`, `webhooks/waha_receiver.py:140`) deixam de poder rejeitar por provedor — passam a rejeitar por conta desconhecida. Custo extra: ~1 fase inteira e a reescrita de 5 testes que hoje codificam a regra antiga.

**Impacto do erro:** decidir NÃO agora e SIM depois é barato. Decidir SIM agora custa a fase toda mesmo que ninguém use.

### D2 — As duas contas espelham no **mesmo conversation provider do GHL** ou em providers separados? *(a decisão mais dura)*
`data/models.py:43` tem um `conversation_provider_id` por instância, e `webhooks/ghl_provider.py:63,90` resolve o adapter só pelo `locationId` — o payload `GHLOutboundPayload` (`:26-52`) **não traz nada** que diga por qual número a conversa corre.

Três caminhos, todos com custo visível:
- **(a) Um conversation provider por conta** — o correto. Exige criar N providers no GHL, coluna por conta, e re-amarrar as conversas existentes. Só isso já é uma fase.
- **(b) Inferir pelo histórico** — olhar por qual conta a última mensagem daquele contato entrou (`services/message_log.py`, se ganhar coluna de conta). Funciona na maioria das vezes e **erra em silêncio** no resto.
- **(c) Aceitar que só a conta primária espelha no CRM** — as outras contas são "só IA", sem operador respondendo pelo GHL. Custo zero de engenharia, custo de produto alto.

**Sem escolher isto, metade das respostas do operador sai pelo número errado — e é marcada `delivered` no CRM (`ghl_provider.py:233-235`), então ninguém percebe.**

### D3 — O mesmo lead falando com duas contas nossas tem **uma memória ou duas**?
`services/chat_memory.py:18` → `f"{location_id}_{phone}"`. Se a resposta for "duas", a chave muda **e** `_add_message_sync` (`chat_memory.py:58`) quebra: ele re-deriva o location_id com `split("_", 1)`, então o segmento novo tem que vir **depois** do location_id (`{location_id}_{account}_{phone}` quebra; `{location_id}_{phone}@{account}` não). Chaves antigas precisam continuar legíveis ou o histórico dos 6 agentes some da tela.

### D4 — Lead qualificado numa conta **pausa a IA na outra**?
Hoje sim, por construção: `UniqueConstraint("location_id","phone")` em `data/models.py:246`, lido em `services/qualification_engine.py:43-55` e `services/inbound_pipeline.py:388-397`. Se a resposta for "não", isso é migration + backfill numa tabela de produção com dados reais, e a idempotência de `services/qualification_handler.py:49-56` muda de sentido. **Estado irreversível na prática** — o próprio código já documenta isso em `services/agent_provisioning.py:303-317`.

### D5 — Duas contas falando com o mesmo lead = **um contato no CRM ou dois**?
`ContactMapping` tem PK `location_id + phone` (`data/models.py:195`). "Um contato" provavelmente é o desejado, mas hoje é **premissa implícita, não decisão**. Vale carimbar.

### D6 — Dois bots de **Telegram** na mesma instância entram no escopo?
Se sim, a URL do webhook **precisa** ganhar segmento de conta — o update do Telegram não identifica o bot receptor, não há plano B (`webhooks/telegram_receiver.py:54`). E migrar exige `setWebhook` novo em cada bot já em produção. Se não, o Telegram fica fora da metade B e economiza uma fase.

### D7 — "Mais de um agente" significa só **contas diferentes**, ou também **dois agentes na mesma conta**?
O dono disse contas diferentes. Mas se algum dia for "dois agentes no mesmo número" (ex.: comercial e suporte, roteados por horário ou por palavra-chave), isso **não é conta** — é um roteador de intenção, e nenhuma linha deste plano ajuda. Vale carimbar que está fora.

---

## Fase 0 — Parar de mentir com os agentes que já existem
**Não depende de nenhuma decisão. Não é feature. Corrige bug de hoje.**

Sete queries pegam **um agente arbitrário da instância**, algumas sem sequer filtrar canal. Com whatsapp + telegram numa instância (que já é o caso), o comportamento já é indeterminado:

| Arquivo:linha | O que faz errado hoje |
|---|---|
| `services/inbound_pipeline.py:101` | `debounce_seconds` de um agente qualquer vale para todos os canais |
| `services/inbound_pipeline.py:388` | `ai_is_enabled` (whatsapp_only) lê `is_active` de um agente qualquer — **pausar o Telegram pode desligar a IA do WhatsApp** |
| `webhooks/zapi_receiver.py:427` e `:450` | mesma leitura de debounce, **duplicada** — corrigir num lugar não corrige no outro |
| `admin/ai_agent.py:509` e `:570` | tela de Histórico desenha progresso com `qualification_fields` de outro agente |
| `admin/ai_agent.py:1134` | tester aplica o guardrail outbound do `form_data` do agente errado |
| `services/ai_service.py:549` | `.first()` **sem `order_by`** — não determinístico por definição |

**Trabalho:** passar `channel` onde falta, `order_by(AIAgent.id)` onde o `.first()` for legítimo, e deduplicar as duas leituras de debounce num helper único. **Nenhuma dessas queries deveria sobreviver à Fase 1** — mas Fase 0 é entregável isolado e serve de rede se as fases seguintes atrasarem.

**Teste que prova:** instância com agente WhatsApp ativo + agente Telegram inativo → inbound do WhatsApp responde. Hoje isso é sorteio.

---

## Fase 1 — A identidade do agente vira `id` (e a tela lista por nome)
**Entrega visível: o pedido do dono, para os agentes que já podem coexistir.**

O dado já existe. `GET /admin/agents/{location_id}/list` (`admin/ai_agent.py:205`) **já devolve `id`, `name`, `channel`, `is_active`, `model`** para todos os agentes do tenant. Quem joga fora é o front: `dashboard.js:1216` faz `data.agents.map(a => a.channel)`.

**1.1 — Backend: rotas por id, com `channel` aceito como fallback deprecado**
Cada rota ganha `agent_id` opcional; quando presente, ele vence. Alvos: `save` (`:131`), `GET agent` (`:238`), `DELETE` (`:285`), `link/unlink-channel` (`:323`,`:369`), `inherit-keys` (`:387`), `improve-prompt` (`:1183`), `form-data` (`:1319`), `test` (`:1132`), `prompt-history` (`:1650`). Manter o fallback por canal durante 1-2 deploys evita que um front em cache escreva no agente errado.

**1.2 — `prompt_history` por `agent_id`** (`services/prompt_history.py:100`, `:156`)
A coluna `AgentPromptHistory.agent_id` (`data/models.py:294`) **já existe, já é FK, e já é gravada** pelos 5 pontos de escrita. Nenhuma migration, nenhum backfill de linhas novas. Isso corrige o pior bug latente do histórico: `restore_version` (`prompt_history.py:155-166`) busca o agente vivo por `(location_id, channel).first()` e **escreve o prompt restaurado no agente errado**. Linhas antigas sem `agent_id` (se houver) caem num fallback por canal.

**1.3 — Front: lista de agentes**
- `admin/dashboard.py:300`: `agent_map[a.location_id] = a` vira `agent_map[loc] = [agentes]`. O card mostra **contagem + estado agregado** ("2 no ar, 1 pausado") em vez de um estado só (`dashboard.html:224`).
- `web/templates/partials/modals.html:927` (aba "Agente IA", hoje só dois botões) ganha a **lista por nome**, alimentada por `/list`.
- `dashboard.js:982` `openAIAgentModal(btn)` — hoje recebe o **card do tenant** e lê ~28 `data-ai*` inlinados (`dashboard.html:160-188`). Vira `openAIAgentModal(agentId)` + `fetch`. **É a mudança de maior risco da fase**: 7 blocos de preenchimento (config, TTS, qualificação, cadastro) mudam de fonte de dados.
- `modals.html:1325`: `<input id="ai_channel">` ganha um irmão `<input id="ai_agent_id">`; os 6 leitores de `ai_channel` em `dashboard.js` (561, 1186, 1329, 1335, 1571, 2227, 2985) passam a ler o id.
- Matar `dashboard.js:1186` (`ai_channel = 'whatsapp'` forçado ao abrir) — hoje qualquer agente aberto começa se dizendo WhatsApp; salvar sem trocar de aba grava por cima do agente WhatsApp.
- `dashboard.js:1224` `_renderChannelTabs`: parar de reconstruir a lista com **regex sobre o atributo `onclick`** (`:1341-1345`, `:1354-1358`). Enquanto o canal for o identificador de UI, dois agentes no mesmo canal colapsam num item ao re-renderizar.

**1.4 — Consequência boa:** a partir daqui, a Fase 2 é uma mudança de *chave*, não de *contrato*. A UI já sabe falar "aquele agente".

**Ainda não muda:** `UniqueConstraint("location_id","channel")` continua. Continua sendo impossível criar o 2º WhatsApp. `dashboard.js:1520` (`openAddChannelModal` filtra `c !== 'whatsapp'`) continua proibindo — corretamente, por enquanto.

---

## Fase 2 — O schema ganha o conceito de CONTA
**Aqui começa a metade B. Só entrar depois de D1, D6 e D7 respondidas.**

**2.1 — Tabela `channel_accounts`** (aditiva, ninguém lê ainda)
`id`, `location_id` (FK), `channel`, `provider`, `label` (nome humano: "Comercial 11 9xxxx"), `external_ref` (o `instanceId` da Z-API / a `session` do WAHA / o `bot_username` do Telegram), e as credenciais que hoje moram em `data/models.py:25-37`. `UNIQUE(location_id, channel, external_ref)`.

Nota do que **já ajuda**: os adapters são **stateless** (`ZAPIChannel`/`WAHAChannel` não guardam credencial em `self`), e os singletons de transporte já recebem credencial por argumento — `services/zapi_service.py:80,103,126,150`, `services/telegram_service.py:33`, `services/waha_service.py:104-138`. **A camada de transporte já é multi-conta.** Só a *fonte* da credencial é que é única.

**2.2 — Backfill 1:1 dos tenants existentes** (mesma migration, idempotente)
Cada tenant com credencial preenchida vira 1..N contas. `waha_session` já é coluna livre com fallback para `location_id` (`channels/whatsapp/waha.py:101`) — **as sessões existentes não precisam ser renomeadas**, elas viram o `external_ref` da conta.

**2.3 — Dual-write nas colunas planas do Tenant, por alguns deploys**
Escrever na conta **e** na coluna plana. Isso é o que torna o rollback possível: se a Fase 3 der errado, o código antigo ainda lê o tenant e funciona.

**2.4 — `ai_agents.account_id`** (nullable), backfill para a conta única do canal, depois:
`UNIQUE(account_id)` no lugar de `UNIQUE(location_id, channel)`. **O precedente exato desse ritual já existe e foi testado**: `alembic/versions/011_add_agent_channel.py:45-70` dropa a unique antiga (inclusive o índice unique implícito, via `inspector`) e cria a nova, tudo idempotente. Copiar de lá.

⚠️ **A migration não pode dropar a unique antiga na mesma migration que adiciona a coluna**, se houver janela em que o código velho ainda roda. Ordem segura: migration A (coluna + backfill, unique antiga intacta) → deploy → migration B (troca a unique). E lembrar da posse: `ALTER TABLE` exige POSSE — objeto criado fora do app precisa de `ALTER TABLE ... OWNER TO millochat_app` (já documentado no CLAUDE.md, e já derrubou boot antes).

**2.5 — `linked_to_channel` → `linked_to_agent_id`** (se D7 confirmar >1 agente por canal)
`services/ai_service.py:557-564` resolve alias por `(location_id, target_channel).first()`. Com dois WhatsApps, "herdar do WhatsApp" não diz de qual. Coluna nova + backfill; `admin/ai_agent.py:323-330` valida o alvo.

**2.6 — `AgentDraft.account_id`** (`data/models.py:501` só tem `channel`, e `_CAMPOS_EDITAVEIS` em `draft_service.py:83` só aceita `'channel'`). Sem isso o wizard não tem onde registrar "este rascunho é pro número X".

---

## Fase 3 — A conta viaja do webhook até o roteador
**A informação existe no fio; o código a descarta.**

**3.1 — `ParsedMessage.account_id`** (`channels/base.py:14-47`)
Campo aditivo, opcional. `waha_receiver.py:164` já usa `dataclasses.replace` para reescrever campos pós-parse, então o padrão já está lá. `channel` e `provider` já viajam intactos até o log (`services/message_log.py:141-142`) — **é acrescentar uma dimensão, não redesenhar**.

**3.2 — WAHA: ler o que já chega.** `payload["session"]` está no topo de todo evento (confirmado em `tests/test_waha_inbound.py:37`) e é **ignorado** por `webhooks/waha_receiver.py:76` e por `channels/whatsapp/waha.py:217-287`. Nenhuma URL muda, nenhuma sessão precisa ser re-registrada. É a conta mais barata de identificar do sistema inteiro.

**3.3 — Z-API: ler `instanceId` do payload.** Hoje o grep por `instanceId` no caminho inbound só acha `tenant.zapi_instance_id` (do banco, nunca do corpo). `channels/whatsapp/zapi.py:94` monta o `ParsedMessage` sem identidade de receptor. ⚠️ **Não verificado:** se a Z-API envia `instanceId` em *todos* os tipos de evento (texto, áudio, imagem, status). Se não enviar em algum, esse tipo precisa de fallback ou de URL discriminada.

**3.4 — Telegram: a URL precisa mudar.** `/webhook/telegram/{location_id}/{account_id}`, mantendo a rota antiga como "conta padrão" para os bots já registrados. Migrar exige `setWebhook` novo em cada bot de produção — **operação manual, contar isso no plano**.

**3.5 — `services/ai_service.py:596` `process_incoming_message(...)` ganha `account_id`.**
É a **fronteira onde a informação se perde hoje**. Os receivers passam literal (`zapi_receiver.py:157` `channel="whatsapp"`, `telegram_receiver.py:176` `channel="telegram"`). Sem alargar esta assinatura, nenhuma correção a jusante resolve.

**3.6 — `_get_agent_for_tenant_sync` (`ai_service.py:549`) filtra por `account_id`**, e o `_engine_cache` (`:540`) é chaveado por `account_id`. Hoje dois agentes no mesmo canal disputariam o mesmo slot de cache e a invalidação por `updated_at` (`:582`) faria **thrash permanente**, com risco de servir o engine errado na janela entre leitura e uso. Os `pop` de invalidação (`:553`, `:578`) idem.

**3.7 — Chaves derivadas ganham a conta:**
- debounce: `contact_key` em `inbound_pipeline.py:523`, `zapi_receiver.py:444`, `telegram_receiver.py:115` — sem isso, mensagens para a conta A e para a conta B caem no **mesmo buffer**, são fundidas num turno, a task anterior é cancelada (`:540-542`), e **uma das contas simplesmente não responde**.
- memória: `chat_memory.py:18` conforme **D3**.
- `AgentContext` (`services/agent_engine/base.py:33`) — o que vai pro prompt caching e pro tool dispatch.
- `qualification_handler.py:25` e `escalation_handler.py:50` **já aceitam `channel` por parâmetro** e os receivers já passam (`zapi_receiver.py:166`, `telegram_receiver.py:195`). É alargar assinatura, não criar rota.

---

## Fase 4 — A resposta sai pelo número que recebeu
**O princípio já está implementado; falta a credencial.**

`services/inbound_pipeline.py:550` `handle_inbound(adapter, tenant, pm)` **já recebe o adapter por parâmetro** e responde pelo mesmo adapter que trouxe a mensagem (`:488`, `:506`). "Responde pelo canal em que chegou" já é o desenho. O que falta é que a credencial venha da conta e não do tenant.

**4.1 — O contrato `ChannelAdapter` (`channels/base.py:79-96`) troca `tenant` por `account`** em `send_text/send_image/send_audio/send_document/credentials_ok/register_webhook`. Hoje **não existe argumento capaz de dizer por qual número sair**. Como os adapters são stateless, o ciclo de vida dos singletons (`waha_receiver.py:34`, `media_proxy.py:42`) não muda.

**4.2 — `channels/registry.py:22` `resolve_send_adapter` recebe conta**, não tenant.

**4.3 — ⚠️ O caminho LEGADO do `zapi_receiver.py:176-183` e `:220-227`** chama `zapi_service.send_audio/send_text` **direto**, com `tenant.zapi_instance_id/zapi_token/zapi_client_token`, ignorando o adapter. **É o caminho que os tenants Z-API reais usam hoje.** Uma refatoração feita só na camada de adapter passa **ao lado deste arquivo** — os tenants Z-API continuariam com comportamento de conta única e o bug só apareceria em produção. Este arquivo tem que estar na mesma fase, não na seguinte.

**4.4 — Telegram**: `telegram_receiver.py:167` e `_send_reply_to_telegram(bot_token, ...)` (`:198`, `:206-226`). Não existe `TelegramChannel` em `channels/` — ou se cria o adapter, ou o token vem da conta na chamada direta. Decisão de escopo, não de arquitetura.

**4.5 — `webhooks/ghl_provider.py:90`** conforme **D2**. `_map` (`:163-168`) já separa "enviou" de "amarrou o id" e acumula `enviados` — a estrutura aguenta.

**4.6 — `webhooks/media_proxy.py:134`** e o download em background (`inbound_pipeline.py:177`) resolvem sessão via `_waha._cfg(tenant)`. Mídia da conta B seria buscada na sessão da conta A → **404 silencioso, anexo some no CRM**.

**4.7 — `waha_receiver.py:155`** (fallback @lid) consulta `/api/{session}/lids/...` na sessão do tenant → contato entra sem telefone (warning na linha 169).

---

## Fase 5 — Escopos de produto (conforme D3/D4/D5) e observabilidade
Só aqui entram as mudanças que **dependem de decisão de produto**, e por isso vêm por último: são as menos reversíveis.

- **D4** — `QualifiedLead` UNIQUE `(location_id, phone)` → `(account_id, phone)`. Backfill em produção com dados reais. Sem isso, escalar para humano numa conta (`escalation_handler.py:30`) pausa a IA de **todas** as contas para aquele telefone.
- **D3** — `chat_memory.py:18` + `ai_service.py:336` (a chave vem do `location_id` **do agente**, não da conta que recebeu).
- **D5** — `ContactMapping` (`data/models.py:195`).
- **`services/message_log.py:113,140`** e `data/models.py:344` ganham coluna de conta. Sem isso o painel mostra as duas conversas como **uma thread só**, intercalando números sem marcador — e não há como auditar por qual número a mensagem saiu.
- **Métricas** para os erros silenciosos (ver §4 abaixo).

---

## Fase 6 — Wizard e cadastro de contas na UI
- `services/agent_wizard.py:44` `canais_disponiveis(tenant)` deriva os canais das **colunas planas** — no máximo 1 WhatsApp + 1 Telegram. **É onde a etapa "canal" vira etapa "conta"**, e é o único produtor de `dados.canais` consumido por `dashboard.js:806-819` e pelo guard `pode_publicar` (`:197`).
- `agent_wizard.py:89`: a variante "único" **pré-escolhe** quando só há 1 canal ("não perguntar o óbvio"). Com duas contas de WhatsApp a lista continuaria com 1 item e **o operador nunca veria a pergunta que distingue as contas**.
- `services/draft_service.py:240` `_publicar_sync` é um **upsert por canal** (`criou = agente is None`, `:245`): publicar um 2º WhatsApp **sobrescreve o prompt do primeiro** (snapshot `wizard_overwrite`, `:346`) e desfaz o alias dele (`:330`). Vira create-or-update **por conta**. A lógica de preservação de qualificação (`:287-319`) é por-agente e sobrevive intacta.
- `draft_service.py:182` `vai_substituir` ("já existe agente NESTE canal", exibido em `dashboard.js:866`) passa a dizer "nesta CONTA" — ou nada.
- `admin/ai_agent.py:287` (`delete_agent_by_channel` recusa `whatsapp` como "canal principal") e `dashboard.js:1584`: com N números, "o principal" deixa de existir. E `ai_agent.py:293` faz `.delete()` **sobre o filtro**, não sobre uma linha — apaga todos do canal de uma vez.
- `dashboard.js:1520` `openAddChannelModal` deixa de filtrar `whatsapp` e passa a perguntar **conta**, não canal.
- `admin/ai_agent.py:1550` `create-agent-from-submission` fixa `channel='whatsapp'` — uma 2ª submissão de onboarding precisa poder virar um 2º agente.
- Diagnóstico: `admin/inspect.py:139,194`, `admin/diagnostics.py:78,302`, `admin/seed_joorney.py:515`. Baixa prioridade, **alto custo se esquecido** — é a ferramenta que você usa justamente quando o agente errado respondeu.

---

## 3. Migração dos 6 agentes em produção, sem downtime

O princípio: **nenhum deploy pode exigir que o anterior já tenha rodado o backfill.**

1. **Migration aditiva** — `channel_accounts` criada, `ai_agents.account_id` nullable, unique antiga **intacta**. Código em produção não lê nada disso. Rollback = trivial.
2. **Backfill na própria migration** (idempotente, `_table_exists`/`_column_exists`): cada tenant com credencial vira conta; cada agente aponta para a conta do seu canal. Os 6 agentes viram 6 pares agente↔conta 1:1. `waha_session` **não muda de nome** — vira o `external_ref`.
3. **Deploy A** — código lê a conta **com fallback para o tenant** (`if agent.account_id: ... else: tenant`). Comportamento idêntico ao de hoje para todo mundo. Dual-write nas colunas planas.
4. **Verificação** — `SELECT count(*) FROM ai_agents WHERE account_id IS NULL` = 0 e cada agente aponta para conta do mesmo `location_id` e mesmo `channel`. Um endpoint em `admin/diagnostics.py` (gated) que devolve esse pareamento é mais barato que abrir o psql.
5. **Migration B** — `account_id` NOT NULL, `UNIQUE(account_id)`, drop de `uq_ai_agent_location_channel` (ritual copiado de `011:45-70`).
6. **Deploy B** — fallback removido, criação de 2ª conta liberada.
7. **Colunas planas do Tenant só saem depois** — vários deploys depois, e só quando ninguém mais as ler. Elas são a rede de segurança.

**Config não se perde** porque ela nunca esteve no Tenant: as ~50 colunas de config do agente (`data/models.py:89-139` — prompt, model, chaves, TTS, qualificação, `form_data`) **já são por linha**. Duas linhas já guardam duas personas 100% independentes. O que falta é só a **chave de identidade**.

**Migration que falha derruba o boot** (comportamento atual, deliberado). Testar contra um dump real antes, não só contra SQLite — o backfill toca dados, não schema.

**Testes que vão quebrar no caminho:** `tests/test_wizard_rascunho.py` tem ~10 asserts em `filter_by(location_id, channel).first()`; `tests/test_channel_exclusivity.py` tem 13 testes que **codificam** a regra "um provedor por instância" — 4 a 5 deles ficam obsoletos se **D1 = SIM**, e precisam ser **reescritos**, não apagados às pressas.

---

## 4. O que pode dar errado em silêncio

Ordenado por "quanto tempo até alguém notar".

| Falha | Onde nasce | Sintoma para o cliente | Como detectar antes |
|---|---|---|---|
| **Agente errado responde** | `ai_service.py:549` `.first()` sem `order_by`, com 2 linhas no mesmo canal | Lead do número B recebe a persona do número A. Zero erro no log. | Log de 1 linha por turno com `agent_id` resolvido; alerta se `agent.account_id != pm.account_id` |
| **Resposta sai pelo número errado** | `ghl_provider.py:90` (operador no CRM) e `inbound_pipeline.py:488,506` (IA) | Lead recebe resposta de outro número. **Marcada `delivered` no CRM** (`ghl_provider.py:233-235`) | Contador `outbound_account_mismatch`; assert de que o adapter de saída carrega a mesma conta do inbound |
| **Uma das contas para de responder** | debounce fundido: `inbound_pipeline.py:523` | Silêncio total num dos números, intermitente (só quando o mesmo lead fala nos dois) | Métrica de buffers por `contact_key`; teste que injeta 2 inbounds de contas diferentes, mesmo telefone |
| **Memória contaminada** | `chat_memory.py:18` + `ai_service.py:336` | Agente da conta B "lembra" da conversa da conta A e grava por cima | Teste de isolamento; auditar `session_id` distintos |
| **`split("_",1)` quebrado** | `chat_memory.py:58` deriva `location_id` de volta | Histórico some da tela, ou grava no tenant errado | Teste com `location_id` contendo `_` (verificar se o regex de `utils/validators.py` permite) |
| **Sessão WAHA sobrescrita** | `admin/waha.py:180` grava `t.waha_session = session` | Conectar o 2º número **apaga a credencial do 1º**; o 1º continua rodando no servidor WAHA, mandando webhook, órfão do nosso lado | Guarda explícita no connect antes da Fase 2; hoje é um `UPDATE` sem aviso |
| **Mídia 404** | `media_proxy.py:134` + `inbound_pipeline.py:177` (`_cfg(tenant)`) | Áudio/imagem some no CRM, envio reporta sucesso | Métrica de 404 no proxy por conta |
| **@lid sem telefone** | `waha_receiver.py:155` consulta a sessão do tenant | Contato entra sem telefone (warning `:169`) e nunca é ligado ao CRM | O warning já existe — passar a contá-lo |
| **Prompt de produção sobrescrito** | `prompt_history.py:155-166` restaura no agente achado por `(location_id, channel)`; poda de 50 versões (`:53-79`) conta por canal | Prompt de 22k caracteres (Joorney) reescrito; versões de um agente apagadas pelo outro | **Fase 1.2 elimina isso sem migration** — a coluna já está lá |
| **Publish sobrescreve em vez de criar** | `draft_service.py:240` | Wizard "cria" o 2º agente e na verdade destrói o 1º | Teste: publicar 2 rascunhos no mesmo canal ⇒ 2 linhas |
| **Guarda de provedor rejeitando inbound legítimo** | `zapi_receiver.py:278` / `waha_receiver.py:140` retornam 200 e métrica `provider_inactive` | Mensagens somem, provedor vê 200 | A métrica já existe — colocar no `/health` |

**Padrão comum:** quase toda falha aqui retorna **200 e sucesso aparente**. Nenhuma delas aparece num alerta de erro. Isso é argumento forte para investir em uma métrica de coerência (`inbound.account == outbound.account`) **antes** da Fase 3, não depois.

---

## 5. O que NÃO deu para verificar

- **Estado real do banco de produção.** Não acessei o Supabase. "6 agentes" veio do CLAUDE.md. Não sei quantos tenants são, quantos usam WAHA vs Z-API, se algum já tem alias `linked_to_channel` ativo, nem se existem linhas de `AgentPromptHistory` com `agent_id` NULL (o que muda o fallback da Fase 1.2). **Rodar essas contagens é o primeiro passo real do plano.**
- **Se a Z-API envia `instanceId` em todos os tipos de evento** inbound (texto/áudio/imagem/status). Só verifiquei que o código nunca lê o campo. Se faltar em algum tipo, a Fase 3.3 precisa de URL discriminada como o Telegram.
- **Se o WAHA de produção manda `session` no formato do teste.** Confirmei em `tests/test_waha_inbound.py:37`, que é um fixture — não é o payload real da versão do WAHA em uso.
- **Se o GHL permite N conversation providers por location**, e o que acontece com as conversas já amarradas ao provider atual se um segundo for criado. **D2 depende disso e eu não pude confirmar.**
- **Ordem real do `.first()` sem `order_by` no Postgres 17** com 2 linhas. Assumi "arbitrário" porque é o contrato do SQL, não porque observei.
- **Se o rebranding/produto quer contas visíveis ao cliente final** (o número aparece na UI? no CRM?) — isso muda o `label` da conta de detalhe técnico para elemento de produto.
- **Custo em tempo.** Não estimei horas; a coluna "custo" acima é relativa (P/M/G), derivada de quantos arquivos e quantas decisões cada fase toca.

---

## Resumo executável

1. **Rodar as contagens em produção** (o que os 6 agentes realmente são).
2. **Fase 0** — 7 queries. Não depende de nada. Corrige bug de hoje.
3. **Fase 1** — id como identidade + lista por nome. **Entrega o pedido visível**, reversível, sem migration de dados. Inclui a correção grátis do `prompt_history`.
4. **Dono responde D1–D7.** D2 (GHL) é a que pode inviabilizar a metade B como produto.
5. **Fases 2→6** — só depois das respostas, nesta ordem: schema → inbound → outbound → escopos → wizard. Cada fase é deployável isolada, com dual-write como rede.