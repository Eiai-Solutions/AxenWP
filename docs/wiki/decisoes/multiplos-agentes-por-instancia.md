---
type: decisao
status: parcial
updated: 2026-08-17
sources: [services/inbound_pipeline.py, admin/ai_agent.py, services/ai_service.py, data/models.py, channels/registry.py, services/agent_wizard.py, web/static/js/dashboard.js, web/templates/partials/modals.html, tests/test_agente_por_canal.py]
confidence: high
---

# Decisão: mais de um agente por instância

**Status:** metade A **no ar** (2026-08-17). Metade B parada, esperando 3 decisões de produto.

## O pedido

Do Luiz: *"o certo seria eu poder criar mais de um agente e ali separar pelo nome do
agente criado e quando clicado aí sim abrir o modal com os detalhes daquele agente.
Logicamente não pode ter dois agentes no mesmo número e canal, pode ter mais de um
agente no mesmo canal mas se for em uma conta do canal diferente."*

## A leitura que organiza tudo

A frase tem **duas metades com custo de ordem de grandeza diferente**, e elas estavam
embaralhadas:

| | o que é | custo | precisa de decisão? |
|---|---|---|---|
| **A — listar por nome, clicar abre aquele** | trocar a identidade do agente na UI de `channel` para agente concreto | médio, contido no painel | **não** |
| **B — duas contas do mesmo canal** | criar o conceito de CONTA, que não existe em lugar nenhum | grande: schema, inbound, envio, memória, CRM, wizard | **sim, 7** |

**A entrega o que ele vê. B entrega o que ele pediu.** Compartilham exatamente um
pré-requisito, e por isso A veio primeiro: é o degrau que B construiria de qualquer jeito.

Mapeamento com 4 leitores paralelos: **92 pontos de acoplamento**.

## Correção de premissa que mudou o cálculo

O `CLAUDE.md` dizia "6 agentes em produção". **Não é mais verdade** — aquilo era o
Postgres do VPS, antes da migração para o Supabase. Consultado o banco real:

```
tenants com agente: 1
  jVxHh2Elz8MxMurLzwzz  n=1  canais=['whatsapp']  mode=ghl
```

Um tenant, um agente. Isso barateia muito a migração de dados de B — e é a primeira
coisa que o plano mandava fazer: *"rodar essas contagens é o primeiro passo real"*.

## Metade A, entregue

### Os bugs latentes que vinham junto

**9 de 33** queries de `AIAgent` não filtravam canal. Com um agente só o `.first()`
acerta por sorte; ativam no minuto em que o segundo é criado — que é exatamente o que
a metade A passa a permitir. E ativam **em silêncio**:

| onde | o que fazia |
|---|---|
| `inbound_pipeline.ai_is_enabled` | lia `is_active` de um agente arbitrário — **pausar o Telegram desligava a IA do WhatsApp** |
| `inbound_pipeline._debounce_seconds` | usava a janela de um agente qualquer |
| 3 telas do painel | mostravam config de um agente diferente a cada refresh |

Os dois primeiros agora recebem o canal, que já viajava em `pm.channel` até a porta.
As telas ganharam `_agente_da_tela`, com a regra escrita: canal pedido se vier, senão o
de **menor id** — determinístico. Ficaram de fora os 3 usos legítimos (`/list` e
`inspect.py` usam `.all()` de propósito; `seed_joorney` monta pool intencionalmente).

**Correção ao relatório do mapeamento:** ele apontou `ai_service.py:549` como
não-determinístico. Não é — filtra canal E location, e com o UNIQUE atual só existe uma
linha. Vira problema quando o UNIQUE cair, na metade B.

### A tela

Ficou pequena porque **quase tudo já existia e estava sendo descartado**:

- `GET /{loc}/list` já devolvia `id`, `name`, `channel`, `is_active`, `linked_to_channel`
  — o front ficava só com `a.channel`;
- o modal já sabia trocar de agente (`switchChannel` busca `/{loc}/agent?channel=X`,
  popula o form e trata alias com banner). Faltava alguém dizer **qual**.

Uma corrida fechada no caminho: `openAIAgentModal` disparava `loadChannelsForTenant`
sem `await`, e `switchChannel` re-renderiza as abas a partir do DOM. Se a carga
terminasse depois, a aba destacada voltava para WhatsApp com o formulário mostrando
outro agente. A promise agora fica guardada em `window._canaisCarregando`.

### O XSS que a lista introduziu

A revisão adversarial sobre os commits da metade A achou **8 problemas, 1 deles grave
e meu**: XSS armazenado no painel.

O `name` do agente ia para `title="…de ${nome}"` escapando **só `<`**. Aspa dupla no
nome fecha o atributo, e o resto vira **atributo do próprio `<button>`** —
`onmouseover=` executando na mesma página onde `#ai_api_key` e
`#ai_anthropic_api_key` são renderizados. Não há CSP no projeto.

Três coisas que valem virar regra:

1. **Escapar `<` não protege em contexto de atributo.** O perigo ali é a aspa, e o
   ataque não precisa abrir tag nenhuma.
2. **`_escapeHtml` deste projeto NÃO serve para atributo.** Ele é
   `textContent → innerHTML`, que escapa `&`, `<` e `>` — a aspa passa intacta.
   Trocar o escape manual por ele só deslocaria o buraco. Medido: `_escapeHtml('a"b')`
   devolve `a"b`.
3. **`agent_validators.py` só limita o TAMANHO do `name`** (1..200). O contraste está
   no mesmo arquivo: `QualificationField.key` rejeita aspas explicitamente. O `name`
   não ganhou o mesmo cuidado — e a IA Mestre escreve nesse campo a partir da
   entrevista **pública e anônima**.

A correção não foi escapar melhor: a lista passou a ser montada por **DOM**
(`setAttribute` / `textContent` / listener), que é imune aos três contextos de uma vez
— atributo, aspas simples do `onclick`, e conteúdo. Verificado em navegador com a
carga do achado: nenhum handler injetado, nada dispara.

**Regra para o projeto: nada de dado vindo do banco dentro de template string que
vira `innerHTML`.** Se precisar de elemento com dado variável, monte por DOM.

Os outros sete, resumidos: a snapshot do próprio `restore` nascia órfã (reintroduzindo
a ambiguidade que a correção veio resolver); clicar no agente de WhatsApp não buscava
nada e abria com o `dataset` do carregamento da página; a lista não carimbava a
requisição (resposta de uma instância podia pintar dentro do modal de outra); o DELETE
virou erro para canal sem registro e deixava aba fantasma na tela; o tester nunca
recebeu `channel` — **a mensagem do commit afirmava que a tela mandava, e não
mandava**; e `_agente_da_tela` não resolvia alias.

## Metade B: as decisões que são do dono

Estão detalhadas em [[decisoes/multi-agente-plano-completo]]. As três que travam o resto:

1. **Lead qualificado numa conta pausa a IA na outra?** Hoje sim, por construção
   (`UNIQUE(location_id, phone)` em `qualified_leads`). Mudar é migration com backfill
   em dado real — **irreversível na prática**.
2. **As duas contas espelham no mesmo conversation provider do GHL?** O payload do GHL
   não diz por qual número a conversa corre (`ghl_provider.py:63,90`). Sem decidir,
   metade das respostas do operador sai pelo **número errado** — e é marcada
   `delivered`, então ninguém percebe.
3. **Contas do mesmo canal podem usar provedores diferentes** (uma Z-API, outra WAHA)?
   Se não, `channel_policy.py` sobrevive quase intacto e os 13 testes de exclusividade
   continuam válidos. Se sim, é mais uma fase e 5 testes reescritos.

## O padrão que atravessa a metade B

**Quase toda falha aqui retorna 200 e sucesso aparente.** Agente errado responde,
resposta sai pelo número errado, mídia 404 no proxy, memória contaminada — nenhuma cai
em alerta de erro. Isso é argumento para investir numa métrica de coerência
(`inbound.account == outbound.account`) **antes** de mexer no roteamento, não depois.

A mina mais feia mapeada: `admin/waha.py:180` faz `t.waha_session = session`. Conectar
um segundo número **apaga a credencial do primeiro**, que continua rodando no servidor
WAHA mandando webhook órfão.

Relacionado: [[decisoes/multi-agente-plano-completo]] (o plano faseado inteiro) ·
[[decisoes/wizard-de-criacao-de-agente]] (quem cria o agente) ·
[[decisoes/whatsapp-waha]] (o provedor por instância)
