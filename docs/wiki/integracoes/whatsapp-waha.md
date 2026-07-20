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

## Quirk nº 2 — o WAHA reentrega as próprias mensagens

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
