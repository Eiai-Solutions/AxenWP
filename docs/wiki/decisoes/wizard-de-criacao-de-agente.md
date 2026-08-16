---
type: decisao
status: solid
updated: 2026-08-16
sources: [services/agent_wizard.py, services/draft_service.py, services/agent_provisioning.py, alembic/versions/033_agent_drafts.py, web/templates/partials/modals.html, tests/test_agent_wizard.py, tests/test_wizard_rascunho.py]
confidence: high
---

# Decisão: o wizard deriva as etapas do tenant

**Status:** no ar (2026-08-16). Botão "+" abre a criação por etapas.

## O problema

O cadastro de agente era um formulão só, e todo tenant via o mesmo. Isso já era ruim
de preencher; virou **errado** quando o produto passou a ter mais de um arranjo:
com GHL integrado, sem CRM (`whatsapp_only`), com WhatsApp, com Telegram, com os dois.

A pergunta do Luiz foi exatamente essa: *"a parte da integração com o CRM só faz
sentido ter se for o CRM integrado, antes era assim, mas agora tem várias opções"*.

O jeito óbvio — `{% if tem_crm %}` espalhado pelo template — quebra em dois lugares:
o template passa a decidir regra de negócio, e o backend continua aceitando o campo
escondido de quem postar direto na rota.

## A decisão

**Quais etapas existem é uma função pura do tenant**, em
`services/agent_wizard.etapas_para(tenant)`. O template só desenha o que a função
devolveu; a validação de publicação (`pode_publicar`) usa **a mesma lista**. Uma
fonte de verdade para desenhar e para autorizar.

### A regra que não é óbvia: sem CRM a etapa não some, ela muda

Foi a tentação errada. Sem CRM, "qualificação" continua existindo — o que muda é o
que ela significa:

| | com CRM | sem CRM |
|---|---|---|
| `variante` | `"crm"` | `"sem_crm"` |
| o que "qualificar" faz | cria opportunity no GHL (exige pipeline + stage) | grava `QualifiedLead`, que **é** o portão |

Sumir com a etapa tiraria do cliente `whatsapp_only` a capacidade de qualificar
lead — que é metade do valor do produto. Essa distinção já existia no
`qualification_handler`, que branchava certo; quem não branchava era o portão de
provisionamento, e por isso a qualificação da Joorney era **impossível de ligar**.
Ver [[decisoes/produto-saas-fase0]].

Casos de borda que moram no código, não no prompt nem no template:

- Zero canais configurados → uma etapa só, `"bloqueado"`, explicando o que falta.
  Nada de wizard que anda até o fim e falha na publicação.
- `pode_publicar` **falha fechado** com lista de etapas vazia. Um bug que devolvesse
  `[]` não pode virar "não há nada a validar, pode publicar".
- O canal escolhido é validado contra `canais_disponiveis` — postar `channel=telegram`
  num tenant sem Telegram é recusado no serviço.

## Rascunho fora de `ai_agents`

Agente meio-construído **não é linha na tabela de agentes**. Tem tabela própria
(`agent_drafts`, migration 033). O motivo é operacional: `ai_agents` é lida pelo
runtime a cada mensagem que chega. Um rascunho ali seria um agente inconsistente ao
alcance do webhook — e a defesa viraria um `WHERE status != 'draft'` que alguém
esquece em uma das consultas.

## "Derivar, nunca copiar" — e por que isso virou regra

Publicar **deriva** a config de qualificação via `build_agent_provisioning`. Nunca
copia o JSON que o cliente mandou.

Isso não é preferência de estilo: a revisão adversarial pegou o wizard fazendo
exatamente o contrário — copiando a config de qualificação direto do payload,
**passando por cima do portão fail-closed que eu tinha consertado um commit antes**.
Mais 7 achados na mesma passada (agente pausado voltando ligado na atualização,
canal alias não resolvido, `setattr` cego, `form_data` faltando, rascunho sem dono,
guard fail-open). Todos com teste de regressão.

A lição que fica: **um caminho novo de escrita não herda as travas do caminho
antigo.** Toda porta nova para uma tabela sensível precisa passar pelo mesmo portão,
e "passa pelo portão" tem que ser verificável por teste, não por leitura.

Detalhes do publicar que valem lembrar: preserva `is_active` na atualização (não
religa agente que o operador pausou), limpa `linked_to_channel`, e grava o
`AgentPromptHistory` **na mesma transação** — snapshot é invariante, não best-effort
([[decisoes/ia-mestre-portadora-do-metodo]]).

## Em aberto

- A entrevista da Mestre ainda abre em aba nova a partir do passo 2; o certo é
  embutir dentro da etapa ([[decisoes/entrevista-da-mestre]]).

Relacionado: [[decisoes/entrevista-da-mestre]] (a outra porta de criação) ·
[[decisoes/isolamento-operador-cliente]] (quem pode abrir rascunho de quem) ·
[[decisoes/produto-saas-fase0]] (self-service é o objetivo)
