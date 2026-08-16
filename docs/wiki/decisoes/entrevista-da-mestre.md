---
type: decisao
status: solid
updated: 2026-08-16
sources: [services/master_interview.py, services/interview_session.py, services/master_engine.py, public/onboarding.py, alembic/versions/031_agent_interviews.py, web/templates/entrevista.html, tests/test_master_interview.py]
confidence: high
---

# Decisão: duas portas, um gerador só

**Status:** no ar (2026-08-16), pelo link público. Falta a entrada pela tela do painel.

## O problema

Criar agente exigia o cliente preencher um formulário de 14 campos
(`public/onboarding.py`). Quem sabe o que responder, responde bem; quem não sabe —
a maioria — preenche raso, e agente sai genérico.

A ideia era uma entrevista conversacional. O risco óbvio: virar um **segundo caminho
de geração**, divergindo do primeiro com o tempo. É exatamente como `zapi_receiver` e
`inbound_pipeline` já divergiram neste projeto
([[decisoes/reestruturacao-abstracoes-primeiro]]).

## A decisão

A entrevista **não gera agente**. Ela preenche conversando os MESMOS campos que o
formulário preenche, e entrega esse `form_data` ao gerador que já existia:

```
entrevista ─┐
            ├─► form_data ─► generate_agent_spec ─► AgentSpec ─► provisionamento
formulário ─┘
```

`CAMPOS` em `services/master_interview.py` é literalmente a lista de
`public/onboarding.py`. Mudou lá, muda aqui — e há só um gerador para manter
([[decisoes/ia-mestre-portadora-do-metodo]] descreve o gerador).

E vai até o fim: **a entrevista concluída cria uma `OnboardingSubmission`**, a mesma
coisa que o formulário produz (`services/interview_session.py`). Daí para frente o
caminho é o que já existia e já é testado — o operador revisa e manda gerar. A
entrevista é uma **porta nova para um corredor conhecido**, não um corredor novo.

## O loop

Tool-use de verdade, no mesmo padrão de `services/agent_engine/claude_engine.py`
([[decisoes/agente-claude-agent-sdk]]): a Mestre conduz e chama `concluir_entrevista`
quando julga ter o suficiente. **Quem decide que acabou é ela**, não um contador de
perguntas.

Guards que moram no CÓDIGO, porque o prompt pede mas não garante:

- Concluir sem campo obrigatório (`company_name`, `products_services`, `agent_goal`) é
  **recusado** e devolvido como `tool_result` de erro — a Mestre volta a perguntar em
  vez de gerar agente genérico.
- Campo desconhecido ou vazio é descartado; ferramenta desconhecida não quebra o loop.
- O par `tool_use`/`tool_result` fica encostado **também na serialização**: entre
  requests o estado vai para o banco como JSON (`agent_interviews`, migration 031), e
  `tool_use` órfão vira **400 em cascata** na chamada seguinte. Há teste que reidrata o
  estado do JSON e confere o pareamento.

## Custo

Prefixo (system + tools) estável e marcado com `cache_control`. Há teste que **falha se
o prefixo variar entre turnos** — variação invalida o cache em silêncio e o custo
triplica.

Tetos: 40 turnos e 60k tokens de saída por entrevista, mais rate limit de 20/min por IP
na rota. Não é paranoia: a decisão de produto foi expor a entrevista no **link público
anônimo**, gastando a nossa chave Anthropic.

## O escopo é o risco desta rota

O link é público e o token de sessão é conhecido por quem o recebeu. A barreira compara
o tenant do link com o dono da entrevista, **no service** (`interview_session.conversar`,
parâmetro `location_id_esperado`) — um cliente não continua a entrevista de outro nem
sabendo o token. Há teste que tenta o cruzamento.

Erro de infra vira 502 genérico: um anônimo não pode receber stack trace, e há teste
garantindo que a chave da API não aparece no corpo da resposta.

## Detalhes que valem lembrar

- O token da sessão fica no `localStorage` do browser: fechar a aba e voltar **continua**
  a conversa em vez de recomeçar e regastar tokens.
- A tela tem saída explícita "Usar formulário" — quem odeia chat não pode ficar preso.
- `historico_visivel()` filtra o gatilho inicial ("Vamos começar.") e os blocos de
  protocolo; a tela só vê fala de gente e da Mestre.

Relacionado: [[decisoes/ia-mestre-portadora-do-metodo]] (o gerador, e o método que ela
carrega) · [[decisoes/agente-claude-agent-sdk]] (o motor do agente que sai daqui) ·
[[decisoes/produto-saas-fase0]] (self-service é o objetivo desta porta)
