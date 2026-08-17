# Log do wiki (append-only, mais novo embaixo)

## [2026-07-14] add | Bootstrap do wiki + plano de reestruturação
- Criado `docs/wiki/` com índice e log.
- `sintese/visao-geral.md` — o que é o AxenWP + direção SaaS (a partir da análise arquitetural multi-agente da sessão).
- `decisoes/produto-saas-fase0.md` — assessment de prontidão (32/100), 5 bloqueadores, Fase 0 remodelada por "self-service dia 1".
- `decisoes/whatsapp-waha.md` — decisão de trocar Z-API por WAHA (vs Evolution/Baileys), grounded em pesquisa 2026.
- `decisoes/agente-claude-agent-sdk.md` — decisão de trocar LangChain single-turn por Claude Agent SDK (tool-use).
- `decisoes/reestruturacao-abstracoes-primeiro.md` — plano-mãe: ChannelAdapter + AgentEngine, strangler, sprints, 1º/2º PR, 17 decisões abertas.

## [2026-07-14] update | PR #1 em andamento — portas AgentEngine + ChannelAdapter
- Aterrissou porta AgentEngine + LangChainAgentEngine (commit 2091bf2) e contratos ChannelAdapter + ZAPIChannel.parse_inbound (commit af37549), branch feat/pr1-abstracoes-portas.
- 111 testes verdes (era 90). Adicionada seção "Estado de implementação" em decisoes/reestruturacao-abstracoes-primeiro.
- Pendente PR #1: send methods + pipeline compartilhado + rota universal (fatia crítica).

## [2026-07-20] add | Circuito WhatsApp↔CRM fechado — identidade, tokens e providers
- `integracoes/whatsapp-waha.md` — quirk book do WAHA/GOWS. O quirk caro: o remetente chega como `@lid` e o telefone está em `_data.Info.SenderAlt` (ninguém lia). Também: reeco das próprias mensagens (dedup obrigatório), ciclo de sessão, pairing code disponível e não implementado.
- `integracoes/gohighlevel-conversas.md` — a assimetria entre as duas direções: PIT cobre o espelho, mas a saída exige conversation provider do app. Status de entrega dá 401 para token que não é dono do provider (leitura da mesma mensagem dá 200 — é posse, não escopo). Descoberta: as instâncias antigas NUNCA usaram provider; o modo espelho é que sempre funcionou.
- `decisoes/identidade-do-contato.md` — telefone e `@lid` na mesma linha de `contact_mappings` (migration 024), busca por qualquer uma das duas, 4 camadas de resolução por custo crescente.
- Origem: sessão de depuração que fechou os dois sentidos em produção (Eiai Solutions). Commits `b3236b3` (resolução de LID) e `ee6e553` (vínculo das identidades).
- Dívidas registradas: `/webhook/ghl/outbound` sem autenticação nenhuma; `conversation_provider_id` nunca escrito por código algum.

## [2026-07-20] update | Mídia inbound (áudio e arquivos) no WAHA
- `integracoes/whatsapp-waha.md`: novos quirks nº2 (mídia interna + autenticada) e nº2b (áudio sem legenda descartado no pipeline). Renumerado o reeco para nº3.
- Dois bugs silenciosos corrigidos (commit `9c4269b`): anexo com URL localhost:3000 quebrava o espelho inteiro com 422; áudio puro morria em `if not texto: return` antes do STT.
- `media_fetch` no adapter reescreve host interno e passa X-Api-Key em header (nunca na URL — chave global do servidor compartilhado).
- Pendência de infra registrada: entregar o arquivo em si ao CRM exige `WHATSAPP_API_KEY_EXCLUDE_PATH` ou re-hospedagem; arquivo local expira em 180s por default.

## [2026-07-20] add | Proxy de mídia — CRM baixa o arquivo recebido
- `integracoes/whatsapp-waha.md` (quirk nº2): entregar o arquivo ao CRM deixou de ser pendência. Novo `webhooks/media_proxy.py` = `GET /media/whatsapp/{location_id}/{filename}` (commit `71a7733`).
- Escolhido proxy em vez de `WHATSAPP_API_KEY_EXCLUDE_PATH`: não reinicia o WAHA e mantém a chave global privada.
- Provado em produção: proxy sem chave devolve Ogg/Opus 200; path traversal 404.
- Retenção (180s default) fica como env de infra opcional para folga.

## [2026-07-20] add | Mídia recebida durável — player toca no CRM
- `integracoes/whatsapp-waha.md` (quirk nº2): áudio virava player mas não tocava. Causa: GHL hot-linka a URL de entrada e busca lazy, quando o WAHA (retenção 180s) já apagou → 404.
- Solução (`e249daa`): persistir binário no Postgres (`media_blobs`, migration 025) no inbound; proxy serve dali com Range/CORS. Download em background + streaming com teto 25MB; limpeza > 90 dias; rate limit 240/min.
- Verificado por revisão adversarial (5 lentes): sem bloqueadores; chave escrita==lida, Range RFC ok, sem regressão Z-API.

## [2026-07-20] add | Log de mensagens próprio + fix @lid
- `decisoes/log-de-mensagens.md` (nova): tabela `messages` (migration 026) como base do painel de chat próprio; separada de `chat_histories` (memória da IA). Choke point `services/message_log.persist_message`, dedup por índices únicos parciais. Commit `f6e5509`.
- `decisoes/identidade-do-contato.md`: fix do backfill telefone↔@lid ao achar pelo cache (commit `c0f46ae`) — evitava reconectar e duplicava contato; duplicata da Eiai reconciliada no banco.
- Cobertura: WAHA (pipeline) + Z-API (legado) + operador-CRM + status. Telegram fica de fora até migrar ao pipeline.

## [2026-07-22] update | Motor Claude (tool-use) — PR1+PR2 no ar, atrás da flag
- `decisoes/agente-claude-agent-sdk.md`: de plano para IMPLEMENTADO. PR1 (`6786798`) = engine + specs + migration 027; PR2 (`c18ade7`) = fiação + escalation_handler + tools no lugar do marcador.
- Decisões travadas: Anthropic direto (caching), escalar=pausar+nota, Sonnet default.
- Zero regressão nos 5 tenants (langchain default), confirmado por revisão adversarial; 4 achados corrigidos antes do deploy.
- Aplicado o método da skill /criar-agente-sdk: qualificação/escalação viram tools (register_qualified_lead, escalate_to_human) em vez de marcador de texto + heurística morta.

## [2026-07-22] add | Decisão: a IA Mestre carrega o método de criação de agentes
- `decisoes/ia-mestre-portadora-do-metodo.md` (nova, status:draft) — direção travada com o dono, **não implementada**. Registrada ANTES de codar para não construir a Mestre errado.
- Três camadas: skill `criar-agente-sdk` (metodologia) → Mestre (aplica, gera config) → agentes do cliente (config no motor).
- A divisão que evita o erro: **design** (Fórmula/persona/slots/fail-closed) vai na Mestre; **implementação** (lock, idempotência, caching, loop de tools) é código do motor, feito uma vez. A Mestre configura, não reimplementa.
- Output da Mestre deve virar **Agent Spec estruturado** (auditável) em vez de blob de prosa — hoje `master_prompt.py:314` produz texto.
- Em aberto: Mestre segue OpenRouter (`admin/ai_agent.py:772`) ou vira tool-use na Anthropic como o motor? Catálogo de tools maior; versionamento do Spec.
- Backlinks adicionados em `agente-claude-agent-sdk` e `produto-saas-fase0`.

## [2026-07-22] update | Mestre: as duas perguntas em aberto, resolvidas com medição
- `decisoes/ia-mestre-portadora-do-metodo.md` promovida a **status:solid**.
- **Motor:** migrar para Anthropic **single-turn com `output_config`/json_schema**, caller próprio — NÃO tool-use, NÃO reusar `ClaudeAgentEngine` (que não suporta saída estruturada; tool forçada viraria efeito colateral). Driver = output contract, **não** caching: prefixo medido 6.752 chars fica ABAIXO do mínimo de 4096 do Opus (cache seria inoperante) e o padrão esparso dá break-even de 21,7% de hit rate → economia otimista ~$7,56/ano.
- **Tools:** **prefetch determinístico** (2 awaits), não tool-use. Zero graus de liberdade (um só argumento, sempre as duas, sem encadeamento) e o código já existe em `ai_agent.py:374-412`. Decisivo: com tools o fail-closed viraria instrução de prompt em vez de invariante de código — regressão na propriedade de segurança.
- **BLOQUEADOR descoberto:** `create_agent_from_submission` grava 1 de 35 colunas — agente nasce `is_active=False`, sem qualificação, com uma tool só. Ligar o gatilho automático hoje produziria agentes mudos. Vira o passo 1 da ordem corrigida.
- Colaterais: a "Mestre" são 5 call-sites e só 2 usam `master_prompt.py`; rota `regenerate=True` morta na UI; `agent_engine` não existe no painel (troca só via banco); caching pagaria mesmo é em `analyze-prompt` (reenvia o prompt 3x).

## [2026-07-22] update | Passo 1 da Mestre entregue + lição
- `decisoes/ia-mestre-portadora-do-metodo.md`: passo 1 (`379e675`) marcado como feito. `services/agent_provisioning.py` monta a config além do prompt (campos, pipeline/stage, fail-closed real, report auditável).
- Verificação adversarial pegou 4 bloqueadores antes do deploy — 2 de premissa: mudança seria no-op (form público não coleta o campo; testes injetavam à mão) e "fail-closed" estava fail-open.
- **Lição:** a fonte dos campos deve ser o Agent Spec da Mestre (passo 3), não o parser de texto livre — que vira fallback. O encanamento do passo 1 permanece.

## [2026-07-22] update | Passo 3 da Mestre: AgentSpec estruturado (a18888c)
- `decisoes/ia-mestre-portadora-do-metodo.md`: passo 3 marcado feito. `utils/agent_spec.py` (contrato Pydantic que omite IDs de CRM por construção) + `services/master_engine.py` (caller Anthropic `messages.parse`, fail-closed) + `build_agent_provisioning` ganhou `fields_override` (Spec vira a fonte dos campos; parser = fallback).
- Gate PRÓPRIO: `is_configured()` exige chave Anthropic E toggle `MASTER_ENGINE=anthropic` — senão a mesma chave do motor trocaria a Mestre de todos os tenants sozinha. Sem toggle = OpenRouter legado byte-idêntico.
- Floor `anthropic>=0.80.0` (verificado: 0.69 não tem `messages.parse`/`output_format`).
- Falta: validar QUALIDADE com API real (Anthropic vs gpt-4o) antes de ligar o toggle em produção.

## [2026-08-16] add | Isolamento, entrevista da Mestre e migração do motor
- `decisoes/isolamento-operador-cliente.md` (**novo**) — `Organization` + `AdminUser.role`, `require_admin` exigindo operador, barreira por dependência de router e teste de inventário sobre `app.routes`. A ordem importa: criar conta de cliente antes da barreira daria acesso às 86 rotas de `/admin`, incluindo a que devolve `api_key` e prompt em texto puro de qualquer tenant. Corrigido junto o `LIKE` não escapado (location_id contém `_`, curinga em SQL).
- `decisoes/entrevista-da-mestre.md` (**novo**) — "duas portas, um gerador só": entrevista e formulário convergem em `form_data` → `generate_agent_spec`; a entrevista concluída cria uma `OnboardingSubmission`, reusando todo o downstream. Guards no código (obrigatórios, par tool_use/tool_result na serialização), tetos por ser link público anônimo.
- `decisoes/agente-claude-agent-sdk.md` (**atualizado**) — de "não ligado em nenhum tenant" para **6/6 migrados**. Registra o achado: prompt fraco + ferramentas = o agente escala tudo (efeito que não existe no motor legado). Prompt caching medido: 10.757 tokens lidos do cache.
- `decisoes/ia-mestre-portadora-do-metodo.md` (**atualizado**) — status de "não implementada" para "parcialmente"; aponta os dois cérebros que restam (`analyze-prompt`/`master-chat` em OpenRouter inline).
- `decisoes/produto-saas-fase0.md` (**atualizado**) — dimensão de isolamento multi-tenant de 1 para ~3.
- `sintese/contradicoes.md` (**novo**) — Haiku (decidido) x Sonnet (em produção) no loop do agente, sem conta de custo refeita.

## [2026-08-16] add | Wizard de criação e a Mestre que pesquisa a empresa
- `decisoes/wizard-de-criacao-de-agente.md` (**novo**) — as etapas são **função pura do tenant** (`agent_wizard.etapas_para`), e a MESMA lista desenha a tela e autoriza a publicação. A regra que não é óbvia: **sem CRM a etapa de qualificação não some, muda de `variante`** (`crm` → `sem_crm`) — sumir tiraria do cliente `whatsapp_only` metade do valor do produto. Rascunho mora fora de `ai_agents` (migration 033): `ai_agents` é lida pelo runtime a cada mensagem, e rascunho ali seria agente inconsistente ao alcance do webhook.
- **Lição registrada:** um caminho novo de escrita **não herda as travas do antigo**. A revisão adversarial pegou o publicar do wizard copiando a config de qualificação do payload, passando por cima do portão fail-closed consertado um commit antes — mais 7 achados na mesma passada (agente pausado religando, canal alias não resolvido, `setattr` cego, guard fail-open).
- `decisoes/entrevista-da-mestre.md` (**atualizado**) — a Mestre pesquisa antes de perguntar: `consultar_cnpj` (BrasilAPI, sem chave) e `ler_site`. A entrevista é pública e anônima, então "leia esta URL" é SSRF entregue a um estranho; verificado por conexão real que do container se alcança `axenwp_waha:3000`, `easypanel:3000` e `axenwp_postgres:5432`. IP conferido **depois** da resolução (DNS rebinding), cada salto de redirect revalidado, teto de 6 pesquisas (senão o link é proxy HTTP aberto gastando a nossa chave), conteúdo rotulado como dado.
- **Quatro bugs meus, achados medindo contra sites reais** (padaria, drogaria, loja) e não lendo código: limpeza de HTML com `.*?</\1>` custava 2,2s de CPU e travava o event loop inteiro; **consertar com bound de tamanho abriu buraco pior** — tag longa deixava de ser tag e o conteúdo do `<script>` vazava para o contexto do modelo; comentário com `>` dentro vazava o resto; `resp.content` baixava o corpo inteiro antes de truncar.
- **Furos conhecidos, registrados em vez de escondidos:** TOCTOU de DNS (o httpx resolve de novo ao conectar — a defesa de verdade é política de egresso no container); site SPA devolve casca vazia; pesquisa só pelo NOME não existe (sem API de busca no projeto).

## [2026-08-16] update | Busca na web fecha a pesquisa por nome
- `decisoes/entrevista-da-mestre.md` (**atualizado**) — `web_search` server-side da Anthropic (`web_search_20260318`, API estável, sem header beta). Fecha a terceira porta: com só o nome, sem site nem CNPJ, a Mestre não tinha como pesquisar.
- **A distinção que organiza o desenho:** `ler_site`/`consultar_cnpj` rodam AQUI e por isso pedem blindagem de SSRF; `web_search` roda na API — não sai da nossa rede, não tem SSRF, mas é **cobrada por request** e por isso ganha teto próprio. Naturezas diferentes, riscos diferentes.
- **O risco da busca por nome é acertar a empresa errada** — dezenas de "Padaria Aurora" no Brasil, e agente sobre a empresa errada é pior que genérico: parece confiante e está errado. A Mestre confirma antes de tratar como verdade.
- Consequências que viraram trabalho: `pause_turn` (busca devolve turno em pedaços — tratar como fim cortaria a resposta no meio); teto de busca dentro de `estourou_teto`, checado no início de `avancar` para o turno que estourou ainda ser SALVO; e **breakpoint de cache móvel** no histórico, que ficou pesado porque o resultado da busca é reenviado a cada turno.
- **Falta fechar:** verificação contra a API real. Os testes usam cliente falso e passariam com o tipo de ferramenta errado.

## [2026-08-16] fix | Reabrir a entrevista era 502 — e a busca na web verificada em produção
- `decisoes/entrevista-da-mestre.md` (**atualizado**) — a tela chama a rota de turno em TODO carregamento, e a conversa salva termina em `assistant` sempre que a Mestre espera resposta. A API recusa como prefill. **Causa raiz medida:** o modelo responde com blocos `thinking`, e extended thinking não aceita prefill de assistant.
- **A lição é sobre cobertura, não sobre a linha:** não havia teste do caminho de REABRIR, só de avançar. A função estava certa para o caminho testado. O bug era invisível na leitura.
- **Diagnóstico que vale repetir:** a entrevista que estourou foi criada 17:58, quase uma hora antes do deploy das 18:52, e tinha `buscas_web=None` — escrita pelo código antigo. Foi isso que separou "bug meu" de "bug preexistente exposto pelo deploy". Ler a linha no banco custou menos que teorizar.
- **Verificado em produção:** `web_search_20260318` executa (blocos `server_tool_use` + `web_search_tool_result`, `buscas_web=1`), e o breakpoint de cache móvel — antes anotado como "não medido" — leu **19.291 tokens do cache** no turno seguinte à busca.
- Pendente: `interview_session.carregar_para_exibir` existe para reabrir sem gastar LLM e segue sem endpoint que a chame.

## [2026-08-16] fix | Varredura adversarial: 17 achados, 7 corrigidos
Depois do bug de reabrir (classe "caminho sem teste"), rodei uma varredura adversarial de 23 agentes na área — 18 achados brutos, 17 confirmados por refutação. Sete eram meus, do mesmo dia, e quebravam produção:

- **`socket.getaddrinfo` síncrono congelava o event loop INTEIRO** enquanto o DNS não voltava — e junto os webhooks de Z-API/WAHA/Telegram em voo. Um anônimo mandando domínio de DNS lento derrubava atendimento de cliente pagante. Resolve em thread. Verificado em produção: 29 batimentos do loop durante uma leitura de 0,65s.
- **`ler_site` sem prazo TOTAL.** O `timeout` do httpx vale por operação: servidor que goteja um byte rearma o relógio a cada chunk e a leitura nunca acaba. O teste de regressão PENDURA sem o fix — foi assim que confirmei que tem dentes.
- **`stop_reason="max_tokens"` no meio de um `tool_use`** gravava o bloco truncado no banco; ele nunca receberia tool_result, então toda mensagem seguinte virava 400 **para sempre**. O caminho mais provável era o FIM da entrevista, quando `concluir_entrevista` manda os 14 campos e estoura os 1500 tokens.
- **Teto estourado dentro do loop levantava antes de salvar** — as buscas que a Anthropic JÁ COBROU sumiam do banco e cada tentativa refazia as mesmas. Num link público e anônimo o freio de gasto nunca fechava.

**O padrão que liga os quatro (e o de reabrir):** todos são caminhos que nenhum teste exercitava, não erros visíveis lendo a função. `max_tokens`, `pause_turn`, reabrir, DNS lento, servidor gotejante — o código estava certo para o caminho coberto.

**Pendentes, reportados e não corrigidos** (wizard e formulário público, mudança maior): publicar por cima apaga a qualificação de agente que já atende; o checkbox "Qualificar leads" nunca liga; as portas "Conversar com a Mestre"/"Preencher formulário" não devolvem nada ao rascunho (a porta *recomendada* não fecha o ciclo); rascunho travado em canal que sumiu; abas do formulário pulam validação e matam o botão em silêncio; erro no submit apaga as 14 respostas; token morto preso no localStorage sem como recomeçar.

## [2026-08-16] fix | O ciclo das portas do wizard, e publicar que apagava qualificação
- `decisoes/wizard-de-criacao-de-agente.md` (**atualizado**) — os três achados aprovados eram **um bug com três sintomas**: `wizardPorta()` gravava só `{origem}` e abria uma aba, nada voltava, então `prompt`/`spec` ficavam None → `pode_publicar` reprovava para sempre **e** `build_agent_provisioning` recebia `spec={}` e devolvia `qualification_enabled: False` (o checkbox era promessa que nunca se cumpria).
- **O cheiro que denuncia:** `submission_id` e `spec` já existiam, já eram validadas e **já eram lidas no publish**. Campo lido e nunca escrito = falta metade do fluxo.
- Fecha com `POST .../wizard/{id}/importar` + botão "Trazer o que a Mestre escreveu", rodando a MESMA `_run_master` do caminho da submissão. A submissão só vira `processed` no PUBLISH — importar e desistir não pode queimar o que o cliente respondeu.
- **Publicar era destrutivo:** escrevia as 4 colunas de qualificação incondicionalmente e zerava a config vinda do formulário ou da curadoria. O agente seguia conversando e só parava de registrar lead, sem erro no log. Hoje só escreve se o agente está nascendo ou se a derivação ligou; senão preserva e avisa.
- **A lição:** *"derivar, nunca copiar" não implica "sempre escrever"* — são duas regras e eu tinha só uma. Derivar protege contra config mentirosa do cliente; não escrever sem intenção protege contra apagar o que já funcionava.
- Lacunas conscientes registradas: desligar qualificação pelo wizard não existe (é pela tela de Configurar Agente); a entrevista ainda abre em aba nova, com retorno manual por botão.

## [2026-08-16] fix | A correção do wizard estava errada — "não destrutivo" é por coluna
- `decisoes/wizard-de-criacao-de-agente.md` (**atualizado**) — varredura adversarial em cima do meu próprio fix: 8 achados, o primeiro grave e meu. Eu tinha chaveado a preservação **inteira** em `qualification_enabled`. Em `whatsapp_only` o `agent_provisioning` devolve `pronto=True` **sem funil** (deliberado — lá o `QualifiedLead` é o portão), então `enabled=True` entrava no ramo de escrita e gravava `pipeline_id=None` por cima do funil curado, trocando campos com `ghl_field_id` por campos sem mapeamento.
- **O dano é silencioso e não retroativo:** o agente para de criar oportunidade no CRM sem erro no log, e a idempotência de `qualification_handler` impede o reenvio mesmo depois de restaurar o funil — os leads daquele intervalo não chegam nunca.
- **A lição de segunda ordem:** *"não destrutivo" é propriedade por COLUNA, não por bloco.* Um `if` que decide pelas quatro de uma vez erra assim que uma delas tem semântica diferente — que é o caso de `enabled` onde ligar sem funil é legítimo. A regra certa (nascendo → tudo; campos curados → não toca; demais → só preenche buraco) já existia em `admin/ai_agent.py`; eu tinha copiado metade.
- Corrigidos junto: importar só ligava e nunca desligava qualificação (sobravam campos de outro negócio ao reimportar); publicar o 1º canal consumia a submissão e o 2º nunca mais importava; a tela descartava `qualificacao_preservada` e mostrava pendências dizendo o oposto do que aconteceu; a chamada **paga** da Mestre rodava antes de validar dono/status do rascunho (id sequencial e adivinhável); o botão TRAZER ficava na tela depois de `origem` virar `manual`; e o `_spec_summary_prompt` da Mestre chegava ao agente pelo formulário mas não pelo wizard — duas portas produzindo agentes diferentes.
- Removidas 3 sondas descartáveis que os agentes de revisão deixaram em `tests/`.

## [2026-08-16] fix | Recomeçar a entrevista, e a resposta quebrada no meio da frase
- `decisoes/entrevista-da-mestre.md` (**atualizado**) — dois problemas que o Luiz achou **usando**, não lendo código.
- **Não havia como recomeçar.** O token vive no `localStorage` de propósito (fechar a aba e voltar não pode regastar), mas sem saída o link devolvia a conversa antiga **para sempre**, inclusive depois de concluída. Era o achado 12/13 da varredura, reportado e não corrigido; a correção de "reabrir não é turno" só tornou visível — antes dava erro, depois restaurava direito e sem saída. O botão só aparece quando **a pessoa já respondeu algo** (na saudação não há o que descartar) e também **quando o turno falha**: com teto estourado ou entrevista sumida, insistir no mesmo token só repete o erro.
- **A resposta chegava partida no meio da frase** — *"…nas Américas / , com / fabricação de talheres"*. Com citações (o que a busca na web produz) a resposta vem em vários blocos de texto CONTÍGUOS, um por trecho citado, e o código juntava com `\n`. A Mestre parecia quebrada exatamente quando acertava.
- **O padrão do dia, de novo:** os dois só aparecem quando alguém usa. Nenhuma varredura de código os encontraria — o primeiro porque o comportamento estava "correto" para o caminho testado, o segundo porque só se manifesta com um recurso que acabara de entrar.
- Verificado no browser contra produção: reload restaura a mesma conversa com o mesmo token e sem chamada à API; recomeçar troca o token (`7xv7q94…` → `Fff1GXQ…`), zera a tela e abre entrevista nova.
