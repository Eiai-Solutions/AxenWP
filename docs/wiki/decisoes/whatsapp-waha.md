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

## Engine escolhido: GOWS (2026-07)
Entre NOWEB/GOWS/WEBJS, escolhido **GOWS** (Go/whatsmeow, WebSocket): baixa latência (browserless) + robustez, é a direção estratégica da WAHA. NOWEB descartado por regressão de crash (2025.11.1). WEBJS fica como **fallback de estabilidade/anti-ban** se GOWS der problema. Como o `WAHAChannel.parse_inbound` é engine-aware (`Tenant.waha_engine`), trocar engine é flip de config.

## Deploy: template one-click do EasyPanel
O EasyPanel tem template oficial do WAHA (Modelos → WAHA). Env-chave: `WHATSAPP_DEFAULT_ENGINE=GOWS`, `WAHA_API_KEY`, `WHATSAPP_HOOK_HMAC_KEY` (assina os webhooks — encaixa no WS1), volume em `/app/.sessions` (persistir sessão). Webhook configurado **por-sessão** (não global), apontando para `/webhook/whatsapp/{location_id}`.

## API REST usada (verificada na doc WAHA)
- Envio: `POST /api/sendText` `{session, chatId:"phone@c.us", text}` · `POST /api/sendImage` `{...file:{mimetype,url}}` · `POST /api/sendVoice` `{...file:{mimetype:"audio/ogg; codecs=opus", data:"<base64>"}, convert:false}`.
- Webhook `message`: `{event, session, payload:{id, from:"..@c.us", fromMe, body, hasMedia, media:{url,mimetype}, notifyName}}`. Cuidado: `hasMedia:true` com `media:null` (mídia não baixada) é possível.

## Gestão de conexão pelo painel (o painel do WAHA fica invisível)
Decisão: criar/conectar/desconectar número é função do **painel do AxenWP**, não do dashboard do WAHA. Modelo: **WAHA compartilhado** (config global em `SystemSettings.admin_waha_url`/`admin_waha_api_key`), **1 sessão por tenant** (`session = location_id`), `whatsapp_provider='waha'` marcado no connect. Fluxo: connect → cria sessão + registra webhook por-sessão (`/webhook/whatsapp/{location_id}`) → status `SCAN_QR_CODE` → painel exibe QR (proxy `/admin/waha/tenant/{loc}/qr`) → escaneia → `WORKING`. `WHATSAPP_HOOK_HMAC_KEY` do WAHA assina os webhooks (encaixa no WS1).

## Deploy (feito)
WAHA no ar via template EasyPanel: `devlikeapro/waha:latest-2026.6.1`, engine **GOWS**, `https://axenwp-waha.i9tdn7.easypanel.host`. Falta conectar a sessão de teste e setar `WAHA_API_KEY` no painel.

## Estado de implementação
✅ Provedor WAHA (flag OFF): `services/waha_service.py`, `channels/whatsapp/waha.py`, `channels/registry.py`, migration 022. Commit `996284a`.
✅ Gestão de sessão + UI no painel: `admin/waha.py` (endpoints), `SystemSettings.admin_waha_*` (migration 023), UI em `dashboard.html`/`dashboard.js` (systemModal + card "Conectar WhatsApp" + wahaModal com QR/polling). Commits `d28642f`, `3c7f2af`. Verificado ao vivo no browser.
⏳ Falta: rota universal `/webhook/whatsapp/{location_id}` + pipeline compartilhado (liga o inbound→IA→resposta) + teste ao vivo com número conectado.

## Decisões abertas
- Topologia: WAHA compartilhado multi-sessão (`session=location_id`) vs instância por tenant — default compartilhado.
- WAHA emite `message.ack`/status equivalente ao `onMessageStatus` da Z-API? Validar no canário.

Relacionado: [[sintese/visao-geral]] · [[decisoes/reestruturacao-abstracoes-primeiro]]
