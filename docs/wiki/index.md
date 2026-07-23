---
type: conceito
status: solid
updated: 2026-07-14
---

# Wiki AxenWP — índice

Cérebro externo do projeto. O código é a fonte da verdade; aqui mora o **porquê** e o **como**. Leia esta página primeiro.

## Síntese
- [[sintese/visao-geral]] — o que é o AxenWP, objetivo, e a direção de virar SaaS · status:solid

## Decisões (ADR vivo)
- [[decisoes/produto-saas-fase0]] — virar SaaS self-service; prontidão ~32/100; 5 bloqueadores e a Fase 0 · status:solid
- [[decisoes/whatsapp-waha]] — trocar Z-API por WAHA self-host (vs Evolution/Baileys) · status:solid
- [[decisoes/agente-claude-agent-sdk]] — trocar LangChain single-turn por Claude Agent SDK (tool-use) · status:solid
- [[decisoes/reestruturacao-abstracoes-primeiro]] — **plano-mãe:** ChannelAdapter + AgentEngine, migração strangler, sprints, 1º PR · status:solid
- [[decisoes/identidade-do-contato]] — telefone e `@lid` são a mesma pessoa; 4 camadas de resolução · status:solid
- [[decisoes/ia-mestre-portadora-do-metodo]] — a Mestre carrega o método (Fórmula) e emite Agent Spec; design vs implementação · status:draft
- [[decisoes/log-de-mensagens]] — log próprio de mensagens como base do painel de chat · status:solid

## Fluxos
- (a registrar) qualificação SDR, debounce/dedup, sync GHL

## Integrações
- [[integracoes/whatsapp-waha]] — quirk book do WAHA/GOWS: `@lid`, reeco de mensagens, sessões · status:solid
- [[integracoes/gohighlevel-conversas]] — espelho vs conversation provider, PIT vs token do app, os dois providers · status:solid
- (a registrar) Z-API, Anthropic, OpenRouter, Groq, ElevenLabs/Fish Audio
