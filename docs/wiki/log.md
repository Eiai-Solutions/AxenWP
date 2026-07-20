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
