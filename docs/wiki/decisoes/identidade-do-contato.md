---
type: decisao
status: solid
updated: 2026-07-20
sources: [channels/whatsapp/waha.py, webhooks/waha_receiver.py, auth/token_manager.py, services/inbound_pipeline.py, data/models.py, alembic/versions/024_contact_mapping_lid.py]
confidence: high
---

# Identidade do contato: telefone e `@lid` são a mesma pessoa

**Decisão (2026-07-20):** o telefone é a identidade preferida, mas o `@lid` é
guardado **junto** na mesma linha de `contact_mappings`, e a busca casa por
qualquer uma das duas.

## O problema

O WhatsApp nem sempre entrega o número. No motor GOWS o remetente chega como
`@lid` (identidade privada); na Z-API o `@lid` aparece em leads de anúncio, onde
a Meta esconde o número. A resolução `@lid → telefone` **não é garantida** — pode
faltar o `SenderAlt` no payload e o servidor pode não responder.

Com o mapeamento guardando uma identidade só, a mesma pessoa virava um contato
novo no CRM toda vez que aparecesse pela outra ponta:

- entrou pelo telefone → depois chega como `@lid` → busca falha → **duplica**
- entrou como `@lid` → depois chega com número → busca falha → **duplica**

## A decisão

`contact_mappings` ganhou a coluna `lid` (`data/models.py:190`, migration
`024_contact_mapping_lid.py`). As duas identidades vivem na mesma linha:

| id | phone_or_lid | lid | ghl_contact_id |
|---|---|---|---|
| `loc_554797838884` | `554797838884` | `198101675561023@lid` | `C1` |

- `get_mapped_contact_id` (`auth/token_manager.py:401`) casa por `phone_or_lid`
  **ou** `lid` — qualquer ponta reencontra o contato antes de criar outro.
- `get_phone_by_lid` (`auth/token_manager.py:424`) recupera o número a partir do
  `@lid`, o que permite **responder** a quem já conversou com a gente.
- `delete_contact_mapping` apaga as **duas** chaves. Deixar a linha alias
  sobreviver a um contato deletado no CRM faria a próxima mensagem reusar um
  `contact_id` morto, em loop e sem auto-cura.

## Ordem de resolução (por custo crescente)

1. **Payload** — `_data.Info.SenderAlt`, síncrono, zero I/O
   (`channels/whatsapp/waha.py:151`). Cobre o caso normal.
2. **Servidor WAHA** — `GET /api/{session}/lids/{lid}`, com cache de 24h
   (`services/waha_service.py:159`). Só quando o payload não trouxe.
3. **Nosso banco** — `get_phone_by_lid` (`webhooks/waha_receiver.py:124`). Quem
   já conversou continua respondível mesmo com o provedor fora do ar.
4. **Desiste** — segue com `@lid` como identidade. O contato entra sem telefone,
   mas o vínculo fica gravado: quando o número aparecer, reencontra o mesmo
   contato em vez de duplicar.

## Por que resolver no parse, e não por HTTP a cada mensagem

Uma revisão adversarial derrubou o desenho HTTP-first com um furo concreto: se a
resolução falhasse numa mensagem e funcionasse na seguinte, o `contact_key` do
debounce (`services/inbound_pipeline.py`) divergiria entre as duas — **duas
janelas de debounce, IA respondendo duas vezes, e um segundo contato no CRM**.
Resolver a partir do payload é determinístico e elimina a classe inteira.

Pela mesma razão, o cache serve o **positivo velho** quando o lookup falha:
identidade estável dentro da conversa vale mais que a atualidade de um dado que
o WhatsApp trata como imutável.

## Rede de segurança independente

`services/ghl_service.py:90` rejeita telefone com `@` antes de montar o payload
do CRM: identidade que não é telefone nunca mais vira `+198101675561023@lid`. A
mensagem entra **sem** telefone — que é honesto — em vez de com um número
impossível. Vale mesmo que toda a cadeia acima falhe.

## Consequências aceitas

- **Redundância de linhas:** quem entrou pelo `@lid` e depois pelo telefone gera
  duas linhas apontando para o mesmo contato. Funcional, e a deleção limpa as
  duas.
- **Sem backfill automático.** Um `UPDATE` cego estouraria `IntegrityError` em
  `qualified_leads` (constraint `uq_qualified_lead_location_phone`) e poderia
  **fundir duas conversas distintas** em `chat_histories` ao reescrever
  `session_id`, interleavando o histórico por `created_at`. Contatos órfãos
  anteriores à correção (ex.: `YIfNiRpSM5ulpHJpu6Pn`, "Lead do WhatsApp
  (Anúncio)" sem telefone) ficam para limpeza manual.
- **A sessão de memória da IA muda** quando a identidade muda: `session_id =
  f"{location_id}_{phone}"`. Conversa iniciada sob `@lid` recomeça do zero ao
  passar a ser identificada pelo telefone. Aceito por ser evento único e raro.

## Relacionado

- [[integracoes/whatsapp-waha]] — de onde vem o `@lid`
- [[integracoes/gohighlevel-conversas]] — o sintoma que expôs o problema
