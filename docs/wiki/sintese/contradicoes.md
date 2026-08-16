---
type: conceito
status: solid
updated: 2026-08-16
---

# Contradições em aberto

Divergências entre o que o wiki decidiu e o que o código faz — registradas em vez de
sobrescritas caladas. Cada item aponta os dois lados e quem precisa decidir.

## 1. Modelo do loop do agente: Haiku (decidido) x Sonnet (em produção)

- **O wiki decidiu** ([[decisoes/agente-claude-agent-sdk]], 2026-07-22):
  `claude-haiku-4-5` para o loop de alto volume, `claude-sonnet-5` só para resumo e
  raciocínio complexo. Motivo: custo ($1/$5 por 1M).
- **O código faz:** `services/agent_engine/claude_engine.py:27` define
  `DEFAULT_MODEL = "claude-sonnet-5"`, e a migração de 2026-08-16 subiu os **6 agentes**
  nele.
- **Por que não resolvi:** ninguém revisitou a conta. O caveat da própria página —
  cache mínimo de prefixo em Haiku é 4096 tokens — pode inviabilizar o caching nos
  agentes de prompt curto (MapInvest tem 1267 chars), que é justamente onde Haiku
  economizaria. Os prompts grandes (Joorney 22k, Kozan 11k) cacheiam bem nos dois.
- **Quem decide:** Luiz. Precisa de medição de custo real por agente antes, não de
  opinião.

## 2. `qualification_enabled` nasce desligado quando não há funil

- **O que acontece:** `services/agent_provisioning.py` desliga a qualificação se o CRM
  não tiver pipeline/stage definidos (fail-closed deliberado).
- **A tensão:** [[decisoes/produto-saas-fase0]] quer self-service; agente que nasce com
  metade da função desligada exige um operador para completar.
- **Status:** consciente, não é bug. Vira problema quando o self-service for real.
