---
type: conceito
status: solid
updated: 2026-08-16
---

# Wiki AxenWP — índice

Cérebro externo do projeto. O código é a fonte da verdade; aqui mora o **porquê** e o **como**. Leia esta página primeiro.

## Síntese
- [[sintese/visao-geral]] — o que é o AxenWP, objetivo, e a direção de virar SaaS · status:solid
- [[sintese/contradicoes]] — divergências entre o que foi decidido e o que o código faz · status:solid

## Decisões (ADR vivo)
- [[decisoes/produto-saas-fase0]] — virar SaaS self-service; prontidão ~32/100; 5 bloqueadores e a Fase 0 · status:solid
- [[decisoes/whatsapp-waha]] — trocar Z-API por WAHA self-host (vs Evolution/Baileys) · status:solid
- [[decisoes/agente-claude-agent-sdk]] — Claude Agent SDK (tool-use); **6/6 migrados em 2026-08-16**; prompt fraco + tools = escala tudo · status:solid
- [[decisoes/reestruturacao-abstracoes-primeiro]] — **plano-mãe:** ChannelAdapter + AgentEngine, migração strangler, sprints, 1º PR · status:solid
- [[decisoes/banco-no-supabase]] — armazenamento sai da VPS para o Supabase; blindagem do `public` antes dos dados; role dedicado · status:solid
- [[decisoes/identidade-do-contato]] — telefone e `@lid` são a mesma pessoa; 4 camadas de resolução · status:solid
- [[decisoes/ia-mestre-portadora-do-metodo]] — a Mestre carrega o método e emite Agent Spec; **motor e tools decididos**; bloqueador: agente nasce desligado · status:solid
- [[decisoes/log-de-mensagens]] — log próprio de mensagens como base do painel de chat · status:solid
- [[decisoes/isolamento-operador-cliente]] — Organization + papel + barreira no router; a ordem que evita o vazamento · status:solid
- [[decisoes/entrevista-da-mestre]] — entrevista e formulário convergem num gerador só; a entrevista vira submission; a Mestre pesquisa CNPJ/site com blindagem de SSRF · status:solid
- [[decisoes/multiplos-agentes-por-instancia]] — listar agentes por nome (no ar); duas contas no mesmo canal exige o conceito de CONTA, que nao existe · status:parcial
- [[decisoes/multi-agente-plano-completo]] — o plano faseado de 92 pontos, com as 7 decisoes do dono · status:proposta
- [[decisoes/wizard-de-criacao-de-agente]] — as etapas são função pura do tenant; sem CRM a qualificação muda de variante, não some; derivar nunca copiar · status:solid

## Fluxos
- (a registrar) qualificação SDR, debounce/dedup, sync GHL

## Integrações
- [[integracoes/whatsapp-waha]] — quirk book do WAHA/GOWS: `@lid`, reeco de mensagens, sessões · status:solid
- [[integracoes/gohighlevel-conversas]] — espelho vs conversation provider, PIT vs token do app, os dois providers · status:solid
- (a registrar) Z-API, Anthropic, OpenRouter, Groq, ElevenLabs/Fish Audio
