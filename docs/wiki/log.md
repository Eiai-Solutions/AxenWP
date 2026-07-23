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
