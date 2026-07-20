---
type: integracao
status: solid
updated: 2026-07-20
sources: [services/ghl_service.py, webhooks/ghl_provider.py, auth/token_manager.py, auth/oauth.py]
confidence: high
---

# GoHighLevel / Axen CRM — conversas

Como mensagem entra e sai do CRM. **A assimetria entre as duas direções é o fato
central desta página** — foi o que confundiu por uma sessão inteira.

## As duas direções usam mecanismos diferentes

| | WhatsApp → CRM (espelho) | CRM → WhatsApp (operador digita) |
|---|---|---|
| Mecanismo | nós chamamos a API | o CRM chama a nossa URL |
| Precisa de | **token** (PIT serve) | **conversation provider** com Delivery URL |
| Endpoint | `POST /conversations/messages/inbound` | `POST /webhook/ghl/outbound` (`webhooks/ghl_provider.py:231`) |
| Depende de OAuth? | não | sim, para *existir* — não para rodar |

**PIT sozinho cobre a entrada.** Precisa do escopo *Edit Conversation Messages*
(`conversations/message.write`) e de `contactId` no payload. O
`conversationProviderId` é **opcional** no modo que substitui o provedor de SMS
padrão — e é assim que rodamos: `services/ghl_service.py` só injeta a chave se
houver valor, e nenhum tenant tem o campo preenchido.

**A saída não é questão de token, é de objeto.** A Delivery URL mora dentro de um
Conversation Provider, que só é criado dentro de um app do Marketplace. Não
existe endpoint público para criá-lo (verificado: `/conversations/providers`
responde "Conversation with id providers not found" — o path é interpretado como
um conversationId). Mas é **configuração feita uma vez**, não dependência de
runtime: em execução o CRM nos chama sem token nenhum, e nossas chamadas de volta
usam o PIT.

> Consequência prática: **cliente novo não precisa de OAuth** para o espelho
> funcionar. Precisa que o app com a Delivery URL esteja habilitado naquela
> sub-conta.

## A URL é UMA para todos os clientes

`POST /webhook/ghl/outbound` — sem `{location_id}` no path. O CRM manda o
`locationId` **no corpo**, e o tenant é resolvido por ele
(`webhooks/ghl_provider.py`). Não existe cadastro de webhook por cliente.

> O painel exigia colar Client ID/Secret a cada instância, o que sugeria o
> contrário. Era bug do formulário: o backend já fazia `ci or
> settings.ghl_client_id` (`auth/oauth.py:43`). Corrigido — a credencial do app é
> config de admin, uma vez.

## Quirk que morde: status de entrega exige o token do app

```
PUT /conversations/messages/{id}/status
→ 401 "You don't have access to the conversationProvider with id: 69283f1c…"
   canonicalCode: CONVERSATIONS_MSG_PROVIDER_NO_ACCESS
```

A **leitura** da mesma mensagem com o mesmo token devolve 200 — não é escopo, é
posse. Só o token do app dono do provider escreve status.

**Efeito visível:** para tenant só-PIT, toda mensagem enviada pelo CRM aparece
como "enviada" para sempre, mesmo que o WhatsApp recuse. O sistema funciona, mas
reporta entrega de forma otimista.

Por isso `get_valid_token` (`auth/token_manager.py:242`) prioriza o token do app
(OAuth) sobre o PIT, com o PIT como fallback se o refresh falhar — perder o
status é ruim, perder o acesso inteiro é pior. Há cooldown no refresh para não
pagar round-trip perdido a cada mensagem.

Erro irmão: `403 CONVERSATIONS_MSG_PROVIDER_NOT_FOUND` acontece ao tentar
atualizar status de mensagem `TYPE_SMS` comum, que não pertence a provider
nenhum. É ruído de log recorrente (visto na Inhance).

## Os dois providers da Eiai Solutions

| Nome | ID | Dono | Estado |
|---|---|---|---|
| **Axen WP** | `69283f1cad530735449fb708` | **nosso** | funcionando |
| Axen API | `6911f2a51d880ff8c5f98ea9` | outra integração do Luiz | Delivery URL dá 404 |

Confirmado por log: o outbound chegou com `conversationProviderId:
69283f1c…` e `from: 'Axen WP'`. Ambos são tipo SMS, então **disputam o mesmo
slot da sub-conta** — não dá para duas integrações atenderem o WhatsApp da mesma
sub-conta ao mesmo tempo.

## Modo espelho ≠ modo provider (descoberta importante)

As instâncias antigas (MapInvest, Kozan, Inhance) **nunca usaram conversation
provider**. Todas as mensagens delas são `TYPE_SMS` com `conversationProviderId:
None` — inclusive as respostas da IA, que aparecem "from MapInvest" porque *nós*
as registramos via API com `direction: outbound`.

Ou seja: o modo que sempre funcionou é o **espelho** — a conversa acontece no
WhatsApp e o CRM é um registro dela. "Digitar no CRM e chegar no WhatsApp" nunca
funcionou em nenhuma instância; não é recurso que quebrou, é recurso que não
existia de ponta a ponta até 2026-07-20.

## Dívida aberta

- `/webhook/ghl/outbound` **não tem autenticação nenhuma** — sem token, sem
  validação de assinatura. Quem souber a URL e um `location_id` válido faz o
  WhatsApp do cliente enviar mensagem. A doc do GHL define o header
  `X-GHL-Signature` (Ed25519) para isso. **Não corrigido.**
- `tenant.conversation_provider_id` existe em `data/models.py` e é lido em vários
  pontos, mas **nenhum código escreve nele** — nunca foi preenchido para nenhum
  tenant. `settings.ghl_conversation_provider_id` (`utils/config.py`) é código
  morto.

## Relacionado

- [[integracoes/whatsapp-waha]] — o outro lado do circuito
- [[decisoes/identidade-do-contato]] — por que o contato vinha sem telefone
