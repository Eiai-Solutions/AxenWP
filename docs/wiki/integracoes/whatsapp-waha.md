---
type: integracao
status: solid
updated: 2026-07-20
sources: [channels/whatsapp/waha.py, services/waha_service.py, webhooks/waha_receiver.py, admin/waha.py]
confidence: high
---

# WAHA — WhatsApp self-host (motor GOWS)

Servidor próprio de WhatsApp não-oficial que substitui a Z-API. Decisão em
[[decisoes/whatsapp-waha]]; esta página é o **quirk book** da integração — o que
só se descobre operando.

**Instância de produção:** `https://axenwp-waha.i9tdn7.easypanel.host` ·
versão `2026.6.1` · engine **GOWS** · tier CORE. Config do servidor (URL + API
key) é **global do admin**, uma vez, para todos os clientes; cada tenant guarda
apenas a sua *sessão* (o número). Ver `services/waha_service.py:25`
(`get_global_waha_config`) e `channels/whatsapp/waha.py` (`_cfg`).

## Quirk nº 1 — o remetente chega como `@lid`, não como telefone

**Este é o quirk que mais custou tempo.** O GOWS entrega o `from` do inbound como
LID (identidade privada do WhatsApp), não como número:

```
[WAHA] inbound de=198101675561023@lid
Mensagem inbound registrada no GHL: phone=+198101675561023@lid   ← número impossível
GHL Outbound abortado: Telefone não encontrado para o contato YIfNiRpSM5ulpHJpu6Pn
```

Consequência em cadeia: o contato nascia no CRM **sem telefone** (nome genérico
"Lead do WhatsApp (Anúncio)"), e por isso a resposta digitada pelo operador não
tinha para onde ir. Dois sintomas, uma causa.

**O payload já traz o número** — ninguém estava lendo. O evento `message` do GOWS
carrega, dentro de `_data.Info`:

| campo | exemplo | uso |
|---|---|---|
| `SenderAlt` | `554797838884:77@s.whatsapp.net` | telefone real do remetente |
| `PushName` | `Luiz Antonio` | nome (o `notifyName` costuma vir vazio) |
| `Sender` | `198101675561023:77@lid` | o LID com device |

Por isso a resolução acontece **dentro do `parse_inbound`, síncrona e sem I/O**
(`channels/whatsapp/waha.py:151`), usando `_phone_from_jid`
(`channels/whatsapp/waha.py:33`) para descartar o device (`:77`) e o sufixo de
servidor. Resolver no parse não é só mais rápido: é **determinístico**. A
alternativa (buscar por HTTP a cada mensagem) foi derrubada numa revisão
adversarial porque uma falha de rede intermitente faria a identidade oscilar
entre mensagens — a mesma pessoa viraria dois contatos no CRM e duas janelas de
debounce, com a IA respondendo duas vezes.

**Fallback HTTP:** o servidor tem um endpoint nativo de conversão, usado só
quando `SenderAlt` não vem (`services/waha_service.py:159`):

```
GET /api/{session}/lids/{lid}   → {"lid":"198101675561023@lid","pn":"554797838884@c.us"}
GET /api/{session}/lids         → lista completa (1145 entradas nessa instância)
GET /api/{session}/contacts/{lid} → {"pushname":"Luiz Antonio"}
```

Cache por `(base_url, sessão, lid)` com TTL de 24h — o vínculo lid↔telefone é
fixo no WhatsApp. **A sessão entra na chave de propósito:** o servidor WAHA é
compartilhado entre tenants e identidade não pode vazar de um para outro. Em
falha *depois* de um sucesso, servimos o valor velho: manter a identidade estável
dentro da conversa vale mais que a atualidade de um dado imutável.

A ordem completa de resolução, por custo crescente, está em
[[decisoes/identidade-do-contato]].

## Quirk nº 2 — a mídia recebida é interna E autenticada

A URL que o WAHA entrega no webhook é inalcançável de fora, por dois motivos
independentes (ambos verificados no mesmo arquivo real):

```json
{"url": "http://localhost:3000/api/files/{session}/{id}.oga",
 "mimetype": "audio/ogg; codecs=opus"}
```

1. **Host interno.** Sem `WAHA_BASE_URL` no ambiente do container, o WAHA usa o
   default `localhost:3000`. O env atual só tem `PORT=3000`.
2. **`/api/files` exige `X-Api-Key`.** `GET` sem chave → `401`; com chave → `200`
   (Ogg/Opus válido). Não distinga isso de 404 testando arquivo inexistente —
   *toda* a API do WAHA responde 401 sem chave; teste o mesmo arquivo real com e
   sem chave.

**Consequências, ambas silenciosas até 2026-07-20:**

- Mandar essa URL como `attachment` para o CRM fazia o GHL recusar o **inbound
  inteiro** com `422 "each value in attachments must be an URL address"` — a
  mensagem se perdia, não só o anexo.
- O STT batia em `localhost` (connection refused) ou, com host corrigido, em
  `401`.

**Como o código resolve** (`channels/whatsapp/waha.py`, `media_fetch`): reescreve
o host interno para o servidor configurado (a partir do marcador `/api/files/`,
então **funciona mesmo sem `WAHA_BASE_URL` setado**) e devolve a chave em
**header**, nunca embutida na URL. A chave é global do servidor compartilhado —
embuti-la na URL a vazaria em log e no histórico do CRM, dando acesso às sessões
de todos os tenants. `ParsedMessage.attachments` passou a significar "URL que o
CRM baixa sozinho"; mídia autenticada vai em `media_url`/`media_mimetype`/
`media_filename` e alimenta só o STT.

**Entregar o arquivo ao CRM — resolvido por proxy** (`webhooks/media_proxy.py`).
O CRM baixa o anexo server-side, sem credencial, e re-hospeda no CDN dele (o
mesmo `static-assets.internal.usercontent.site` que ele usa nos anexos de saída).
Só precisa de uma URL que consiga buscar. Nós expomos
`GET /media/whatsapp/{location_id}/{filename}`: recebe a chamada pública do GHL,
busca o arquivo no WAHA **com** a chave, devolve o binário.

Por que proxy e não `WHATSAPP_API_KEY_EXCLUDE_PATH=api/files/(.*)`:
- não exige reiniciar o WAHA (não derruba a sessão);
- a chave **global** do servidor compartilhado nunca sai do nosso lado.

Segurança (o endpoint é público porque o GHL não manda header): `location_id` e
`filename` com validação estrita; `filename` é basename (sem barra, sem `..`); a
sessão do WAHA vem do **tenant**, não do input, então não dá para pedir mídia de
outra instância nem montar path para `/api/sessions`. `WAHAChannel.public_media_url`
monta o link a partir do basename + `PUBLIC_BASE_URL`; o pipeline o põe em
`attachments` só quando há `media_url`.

> Provado em produção: `GET` sem chave no proxy → `200` Ogg/Opus válido; `GET
> …/sessions` → `404`.

**Player que abre mas não toca → persistência própria.** Para *áudio*, além de
entregar o arquivo, o GHL precisa que a URL continue servindo quando o operador
clica play — e aí a retenção de 180s do WAHA morde de verdade: o GHL **hot-linka**
o anexo de entrada (guarda a nossa URL, não re-hospeda como faz no de saída) e o
busca de forma **preguiçosa**, ao abrir a conversa. Minutos depois o WAHA já
apagou o arquivo → proxy 404 → `<audio>` com `--:--` e play morto.

Solução (commit `e249daa`): persistimos o binário no **Postgres** (`media_blobs`,
migration 025) no momento do inbound, enquanto o arquivo existe no WAHA, e o proxy
serve dali. Detalhes que importam:
- download em **background** (não segura a resposta da IA) e por **streaming** com
  corte no teto de 25 MB (arquivo grande não bufferiza na RAM; cai no proxy ao vivo);
- o proxy serve do store primeiro, **fallback** ao vivo no WAHA para a janela de
  corrida / anexos antigos;
- **Range (206) + `Accept-Ranges` + CORS**: o `<audio>` cross-origin do CRM pede
  a mídia por partes; sem isso não toca nem calcula duração;
- limpeza diária de mídia > 90 dias (como o GHL hot-linka, isso limita por quanto
  tempo o áudio antigo continua tocando); rate limit 240/min no endpoint público.

Chave do blob = o basename da URL do proxy (`{messageId}.{ext_de_exibição}`, já com
`.oga`→`.ogg`), escopado por location — o mesmo nome que o proxy recebe do GHL.

`WHATSAPP_FILES_LIFETIME` no container WAHA deixa de ser necessário para áudio (a
persistência resolve); ainda ajuda como folga para a janela antes do background
terminar, mas não é mais o que sustenta a reprodução.

## Quirk nº 2b — áudio sem legenda era descartado (bug do pipeline, não do WAHA)

`if not texto: return` no `services/inbound_pipeline.py` matava o turno antes do
STT — `is_audio`/`audio_url` eram calculados e jogados fora. A Z-API escapava
**por acidente**: o adapter dela injeta um texto padrão. O WAHA usava `body or
""`, vazio na nota de voz. Corrigido rotulando a mídia sem legenda no parse e
relaxando a guarda para aceitar turno só-áudio.

## Quirk nº 3 — o WAHA reentrega as próprias mensagens

`capabilities.provider_reechoes_own_msgs=True` (`channels/whatsapp/waha.py`):
diferente da Z-API, o WAHA devolve no webhook as mensagens que nós mesmos
enviamos. **Dedup por `provider_message_id` é obrigatório**, senão o agente
responde a si mesmo em loop. O pipeline compartilhado cuida disso.

## Rota de inbound

`POST /webhook/whatsapp/{location_id}` (`webhooks/waha_receiver.py`), registrada
na sessão no momento do connect (`admin/waha.py`). O prefixo é genérico
(`/webhook/whatsapp`, não `/webhook/waha`) de propósito: se um dia trocar o
provedor self-host, a URL registrada nas sessões não muda.

Eventos assinados: `message`, `message.ack`, `session.status`.

> **Armadilha histórica:** a sessão foi criada apontando para essa rota **antes**
> de ela existir no app — todo inbound caía em 404 silencioso. Ao mexer no
> registro de webhook, confira se a rota está no `main.py`.

## Ciclo de vida da sessão

`STOPPED → STARTING → SCAN_QR_CODE → WORKING` (ou `FAILED`). O painel gerencia
tudo (`admin/waha.py`): criar, conectar (QR), reiniciar, desconectar. O painel do
WAHA fica invisível para o operador.

**Conectar por código em vez de QR é possível** e ainda não foi implementado:
`POST /api/{session}/auth/request-code` com `{"phoneNumber": "..."}` devolve
`{"code":"ABCD-ABCD"}`. Suportado por GOWS. A doc recomenda manter o QR como
fallback porque o pairing code nem sempre funciona.

## Relacionado

- [[decisoes/whatsapp-waha]] — por que WAHA e não Evolution/Baileys
- [[decisoes/identidade-do-contato]] — telefone vs @lid como identidade
- [[integracoes/gohighlevel-conversas]] — o outro lado do circuito
