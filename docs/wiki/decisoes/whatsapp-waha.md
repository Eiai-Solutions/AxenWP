---
type: decisao
status: solid
updated: 2026-07-14
sources: [webhooks/zapi_receiver.py, services/zapi_service.py]
confidence: high
---

# Decisão: trocar Z-API por WAHA self-host

## Contexto
O produto vai usar **infra própria de WhatsApp não-oficial** no lugar da Z-API (API de terceiro). Opções avaliadas: **WAHA**, **Evolution API**, ou construir direto no **Baileys**. Critério do Luiz: **menos bugs e manutenção, boa latência**. Experiência ruim prévia com Evolution (instabilidade).

## Decisão
**Adotar o WAHA** (WhatsApp HTTP API, self-host, Apache-2.0), atrás de um `WhatsAppChannel` adapter (ver [[decisoes/reestruturacao-abstracoes-primeiro]]).

## Porquê (pesquisa 2026)
- **Não construir no Baileys direto:** reconstruiria multi-sessão/REST/webhooks/reconexão/mídia/QR + assumiria a superfície de anti-ban e segurança (ecossistema com malware, ex. pacote `lotusbail` roubando credenciais, abr/2026). Transporte de WhatsApp é peso-morto não-diferenciado.
- **WAHA vs Evolution (estabilidade):** Evolution tem codebase reconhecidamente bagunçado, com relatos recorrentes de desconexões/instabilidade/breaking changes em 2025–2026 (bate com a experiência do Luiz). WAHA é vendor único, mais disciplinado, DX mais suave; a comunidade n8n o descreve como "o passo lateral natural saindo do Evolution". WAHA tem **múltiplos engines** (WEBJS/NOWEB/GOWS) — se um trava, troca de engine em vez de ficar refém (Evolution tem só um).
- **Novidade 2026:** WAHA tornou o "Plus" gratuito — multi-sessão/dashboard/S3/proxy vêm na imagem free; só resta um tier opcional de apoio ($5/mês).
- **Latência:** engines WebSocket (NOWEB/GOWS) são leves e rápidos; GOWS (Go) é o mais rápido; WEBJS (browser) é o mais estável/anti-ban, com mais RAM. Engine inicial: **NOWEB**, WEBJS como fallback.

## Trade-offs / consequências
- WAHA **não** tem Cloud API oficial nativa (Evolution tem). Mitigação: como o provedor fica atrás do `ChannelAdapter`, dá para plugar um adapter de Cloud API oficial depois como canal separado.
- **Risco de ban permanece** (protocolo não-oficial, contra ToS; detecção subiu em 2025–2026). Mitigação: 1 número por tenant, warmup, pacing humano (o debounce já ajuda).
- WAHA **re-echoa `fromMe`** → dedup por `provider_message_id` é obrigatório (diferente da Z-API que já filtra). Áudio outbound: WAHA exige base64 em `file.data` (Z-API aceita data-url).

## Decisões abertas
- Engine inicial (NOWEB vs GOWS vs WEBJS) — default NOWEB no canário.
- Topologia: WAHA compartilhado multi-sessão (`session=location_id`) vs instância por tenant — default compartilhado.
- WAHA emite `message.ack`/status equivalente ao `onMessageStatus` da Z-API? Validar no canário.

Relacionado: [[sintese/visao-geral]] · [[decisoes/reestruturacao-abstracoes-primeiro]]
