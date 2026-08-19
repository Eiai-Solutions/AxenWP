---
type: decisao
status: solid
updated: 2026-08-19
sources: [services/precos.py, services/usage_logger.py, data/models.py, alembic/versions/036_custo_real_e_origem.py, admin/dashboard.py, web/static/js/dashboard.js, scripts/backfill_custos.py, tests/test_precos.py, tests/test_migration_custo_origem.py]
confidence: high
---

# Decisão: o painel de custo para de mentir, e separa atendimento de Mestre

**Status:** no ar. O backfill das linhas antigas é manual e opcional.

## O sintoma

A aba MÉTRICAS mostrava **$0,0000** com 2.650 tokens consumidos e 3 chamadas
Anthropic registradas. Não era erro de arredondamento.

## A causa

`save_usage_log` tinha a assinatura

```python
def save_usage_log(..., cost_usd: float = 0.0) -> None
```

e **nenhum dos 6 chamadores passava valor**. Nunca existiu cálculo de custo em
lugar nenhum do projeto — o parâmetro estava lá, o default era `0.0`, e cada
linha nascia valendo zero. A soma de zeros é zero, e o painel exibia isso como se
fosse medição.

O agravante é o formato: `.toFixed(4)`. Uma conversa inteira do agente sai por
frações de centavo, então mesmo com o cálculo certo a tela mostraria `$0.0000`.
Dois defeitos empilhados apontando para a mesma conclusão errada.

## As quatro decisões

### 1. `None` ≠ `0.0` — desconhecido não é grátis

`cost_usd` passa a ser NULL quando o modelo não está na tabela de preços. Zero
some na soma e vira economia imaginária; NULL sobe até a tela como "sem preço" em
ocre, com a contagem de chamadas afetadas ao lado do total.

É a regra que impede o defeito de voltar numa roupa nova: no dia em que alguém
usar um modelo novo, o painel **avisa** em vez de subestimar em silêncio.

### 2. Cache tem preço próprio, e é o grosso da conta

A Anthropic devolve `cache_read_input_tokens` e `cache_creation_input_tokens` em
toda resposta. `claude_engine.py` já os acumulava; `_log_claude_usage` os
descartava. São tarifas distintas:

| | multiplicador sobre a entrada |
|---|---|
| leitura de cache | **0,10×** |
| escrita de cache (TTL 5min) | **1,25×** |

Com ~87% de reaproveitamento de prefixo, somar tudo como entrada erra em ordem
de grandeza — e erra para os **dois** lados: superestima os turnos que releem o
prefixo e subestima o turno que o grava. Duas colunas novas em `usage_logs`.

### 3. Busca web é cobrada por requisição, não por token

`web_search` custa **$10 / 1.000 requisições**, fora dos tokens. A entrevista da
Mestre usa. Coluna `buscas_web`, e o preço entra mesmo quando o modelo não está
tabelado — a tarifa é da ferramenta, não do modelo.

### 4. `origem`: atendimento vs mestre

Era a pergunta do dono ("o custo do agente nos atendimentos **e** o custo da
Mestre") e não havia dimensão no schema para respondê-la. Quatro caminhos da
Mestre gastavam a chave Anthropic do admin e **nenhum registrava nada**:

- `master_engine.generate_agent_spec` — gerar o prompt
- `master_interview.avancar` — a entrevista (por chamada, não no fim: entrevista
  abandonada já foi cobrada)
- `mestre_ciclo.propor_ajuste` — o ciclo de treino
- `sonda.rodar_caso` — a medição de comportamento

A sonda conta como **mestre**, não atendimento: nenhum lead foi atendido ali.
Contá-la como atendimento inflaria o custo por conversa e esconderia o preço de
treinar.

Medido no ambiente de demonstração com volume realista, a Mestre custou **mais**
que o atendimento ($0,21 contra $0,16) — exatamente o tipo de coisa que a conta
única escondia.

## O que ficou de fora, e por quê

- **Preço de Groq, ElevenLabs e OpenRouter.** A skill `claude-api` só é
  autoritativa sobre a tarifa da Anthropic. Chutar seria repetir o defeito com
  mais dígitos. Ficam como "sem preço" até alguém preencher `PRECOS_EXTRA_JSON`
  (`{"servico:modelo": {"entrada": x, "saida": y, "caractere": z}}`, por 1M
  unidades) — sem deploy.
- **Backfill das linhas antigas dentro da migration.** Exigiria a tabela de
  preços dentro dela, e desde a `032` migration que falha **derruba o boot**. É
  `scripts/backfill_custos.py`, com `--aplicar` explícito. Aviso registrado no
  próprio script: linhas anteriores a 2026-08-19 não têm tokens de cache, então
  o valor reconstruído é um **piso**, não o exato.

## A promoção que vence sozinha

`claude-sonnet-5` é o default de todo caminho do projeto e está em preço
promocional ($2/$10 em vez de $3/$15) **até 2026-08-31**. Fixar $3/$15 hoje
superestimaria 50%; fixar $2/$10 subestimaria a partir de setembro. A tabela
guarda a data e o preço cheio, e a regra decide pela data da chamada — inclusive
no backfill, que usa a data da linha e não a de hoje.

Relacionado: [[decisoes/medir-antes-de-melhorar]] (a sonda, cujo custo agora
aparece), [[decisoes/entrevista-da-mestre]] (a busca web que agora é cobrada na
conta certa).
