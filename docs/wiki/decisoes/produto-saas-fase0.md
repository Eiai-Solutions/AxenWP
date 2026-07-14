---
type: decisao
status: solid
updated: 2026-07-14
sources: [data/models.py, admin/dashboard.py, utils/config.py, services/usage_logger.py]
confidence: high
---

# Decisão: virar SaaS self-service + Fase 0

## Direção
Transformar o AxenWP de hub interno operador-cêntrico em **plataforma SaaS multi-tenant**. **Decisões do Luiz:**
1. **Produto nº1 = integração nativa com Axen/GHL primeiro**, mas o CRM deve ser **plugável** (mais CRMs depois + painel de chat próprio) → abstração `CRMProvider` já na Fase 0.
2. **Go-to-market = self-service desde o dia 1.**

## Veredito de prontidão: ~32/100
MVP funcional maduro para uso operado, **não vendível ainda**. Fundação de domínio sólida e preservável ([[sintese/visao-geral]]); precisa de hardening + estado distribuído, não reescrita.

### Scorecard (atual/alvo, de 5)
| Dimensão | Atual | Alvo |
|---|:-:|:-:|
| Segurança & isolamento multi-tenant | 1 | 4 |
| Confiabilidade & erro | 1 | 4 |
| Testes & qualidade | 1 | 4 |
| Prontidão comercial/SaaS | 1 | 4 |
| Escalabilidade & concorrência | 2 | 4 |
| Extensibilidade multicanal | 2 | 4 |
| Modelo de dados & persistência | 2 | 4 |
| Observabilidade & operação | 2 | 4 |
| Plataforma de IA | 2 | 4 |

## 5 bloqueadores nº1
1. Webhooks sem assinatura HMAC (`utils/config.py:36` declara `zapi_webhook_secret`, nunca usado).
2. Segredos de tenant em texto plano (`data/models.py:19-28`); dashboard devolve sem máscara.
3. Estado in-memory process-local quebra multi-worker (`zapi_receiver.py:30,66,70`; sem Redis; `Dockerfile:29` single-worker).
4. Zero garantia de entrega (sem tenacity/circuit/fila; buffer descarta msg; "lead preso pós-qualificação").
5. Multicanal copy-paste (~70% dup) — resolvido pela abstração em [[decisoes/reestruturacao-abstracoes-primeiro]].

## Como "self-service dia 1" remodelou a Fase 0
Puxou para dentro da Fase 0 o que era Fase 1: **conta/usuário + auth real** (o `ADMIN_PASSWORD` único de `admin/dashboard.py:28` não serve p/ N clientes), **painel do cliente** tenant-scoped, **billing/metering + Stripe**. Sem conta+auth+painel+billing não há self-service.

## Os 8 workstreams da Fase 0
WS1 segurança de borda (HMAC + cripto) · WS2 identidade SaaS (Account/User/Membership, JWT+bcrypt) · WS3 estado distribuído (Redis + multi-worker) · WS4 confiabilidade (retry/circuit/fila; fix lead preso) · WS5 billing/metering/Stripe (`cost_usd` Float→Numeric) · WS6 painel do cliente · WS7 `CRMProvider` (GHL 1ª impl + InternalCRM) · WS8 qualidade/CI (não existe `.github/` hoje; teste de isolamento multi-tenant).

**Esforço:** ~25–27 semanas-dev bruto; **GO-LIVE MVP ~8 semanas** com 2–3 devs em paralelo, rodando em `WEB_CONCURRENCY=1` (multi-worker/Redis fica pós-go-live). 6 sprints. Caminho crítico infra: WS8-slice → WS1 → WS3 → WS4.

## Risco #1 de execução: numeração de migration
Migrations vão até **021** (não 18, como o CLAUDE.md diz — doc drift). Alocação fixa: WS2=022, WS1=023/024, WS5=025, WS4=026, WS7=027. Nomear um "migration sequencer". ⚠️ A reestruturação também reivindica 022/023 — **coordenar** com [[decisoes/reestruturacao-abstracoes-primeiro]].

## Decisões de produto ainda abertas
Modelo de pricing (por conversa/lead/msg/flat — default: Free + 1 pago), nível de compliance-alvo, quais canais depois.

Relacionado: [[sintese/visao-geral]] · [[decisoes/reestruturacao-abstracoes-primeiro]] · [[decisoes/whatsapp-waha]] · [[decisoes/agente-claude-agent-sdk]]
