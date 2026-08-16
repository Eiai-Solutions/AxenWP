---
type: decisao
status: solid
updated: 2026-08-16
sources: [services/admin_auth.py, admin/dashboard.py, data/models.py, alembic/versions/030_organizations_e_papel.py, tests/test_barreira_operador.py, admin/ai_agent.py]
confidence: high
---

# Decisão: barreira operador ↔ cliente antes de existir cliente

**Status:** implementada e no ar (2026-08-16). Nenhum usuário cliente criado ainda —
de propósito, ver "a ordem é a decisão".

## O problema, em uma frase

`verify_admin` perguntava apenas *"o cookie resolve para algum `AdminUser` ativo?"* —
não existia **uma linha** no projeto que perguntasse "este usuário pode tocar neste
`location_id`?".

Autenticação madura, autorização zero.

## A ordem é a decisão

No instante em que existisse uma linha em `admin_users` com papel de cliente, esse
cliente passaria nas **86 rotas** sob `/admin`. Entre elas
`GET /admin/agents/{location_id}/agent` (`admin/ai_agent.py`), que devolve `api_key`,
`anthropic_api_key`, `elevenlabs_api_key` e o prompt **em texto puro de qualquer
tenant**.

Criar a conta do cliente antes da barreira não seria "risco": seria o vazamento no
mesmo commit. Por isso a primeira entrega do painel do cliente
([[decisoes/log-de-mensagens]] é a fundação de dados dele) **não tem tela** — é papel,
escopo e guard. É a única parte do plano cujo custo CRESCE se for adiada: todo endpoint
escrito antes da barreira precisaria ser reauditado depois.

## O modelo: Organization, não tenant

`Organization` (`data/models.py`) é a EMPRESA cliente; `Tenant` é um **número de
WhatsApp**. A mesma empresa pode ter comercial e suporte em números distintos, e quem
paga é ela. Por isso o vínculo do usuário é com a Organization.

`tenants.organization_id` é **nullable de propósito**: tenant sem dono não é visível por
cliente nenhum. Falha fechada, e os 5 tenants que já existiam seguiram funcionando sem
tocar em nada (migration 030).

## Onde a barreira mora

**1. Na dependência do router, não na rota.** `/admin` foi dividido em
`router_publico` (só login/logout) e `router`, que recebe `Depends(require_admin)`
(`admin/dashboard.py`). Rota nova nasce fechada.

O padrão anterior — `verify_admin` devolvendo `bool` e cada rota lembrando de checar —
é literalmente o que deixou 25 rotas abertas em `admin/ai_agent.py`
([[decisoes/reestruturacao-abstracoes-primeiro]] registra o incidente). O inventário
desta entrega ainda encontrou `/admin/tenant/{id}/qrcode` respondendo 200 com corpo de
erro em vez de recusar.

**2. `require_admin` RECUSA, não informa.** 401 sem sessão, **403** para cliente
autenticado — 401 o mandaria ao login num loop sem explicação.

**3. O escopo é lido do BANCO a cada request**, nunca do cookie
(`Principal` em `services/admin_auth.py`). O token de sessão é HMAC sobre
`(username, password_hash)` ([[decisoes/log-de-mensagens]] não cobre isto; ver
`CLAUDE.md` → Convenções → Auth admin): se o escopo viajasse no cookie, tirar um tenant
de um cliente só valeria depois que ele trocasse a senha. Revogação precisa ser
imediata, e há teste que prova.

**4. `Principal.alcanca(location_id)` é a única fonte da verdade** sobre "pode tocar
neste tenant?". Operador tem `locations` vazio e `is_operator=True` — a ausência de
escopo nunca deve ser lida como "vazio = nenhum" nem "vazio = todos"; cheque
`is_operator` explicitamente.

## O que sustenta isso depois

`tests/test_barreira_operador.py` tem um **teste de inventário**: varre todas as rotas
registradas sob `/admin` e falha se alguma responder a um cliente. É o que mantém a
barreira quando ninguém estiver mais olhando o arquivo — não depende de disciplina.

## Bug de vazamento corrigido junto

`ChatHistory.session_id.like(f"{prefix}%")` em 3 pontos de `admin/ai_agent.py`, com
`prefix` vindo de `location_id`. **Os location_ids contêm underline**
(`wp_9fe4c6ef7915`), e `_` é curinga de um caractere em SQL — o filtro de um tenant
casava com o `session_id` de outro que diferisse só naquela posição. Agora escapa com
`ESCAPE` explícito.

## O que falta

- As 4 rotas que recebem id sequencial sem `location_id` no path
  (`/admin/agents/prompt-history/{id}`, `/restore`, `/onboarding/submissions`,
  `.../create-agent`) continuam operator-only, então não são exploráveis hoje — mas
  precisam de escopo quando/se forem expostas ao cliente.
- Router `/app` do cliente, onde as rotas **não recebem `location_id` pela URL** (ele
  sai do principal). Isso elimina por construção a classe de bug "esqueci de checar o
  path param", que é o defeito de 36 rotas hoje.

Relacionado: [[decisoes/produto-saas-fase0]] (a dimensão "Segurança & isolamento
multi-tenant" que isto move) · [[decisoes/log-de-mensagens]] (os dados que o painel do
cliente vai ler) · [[decisoes/banco-no-supabase]] (a outra camada de isolamento, no
banco)
