---
type: decisao
status: solid
updated: 2026-08-19
sources: [services/sonda.py, services/roteiros/comportamento_sdr.json, services/mestre_ciclo.py, tests/test_agente_casos.py, tests/test_ciclo_confirma_amostra.py]
confidence: high
---

# Decisão: no primeiro turno de um SDR, o texto É o produto

**Status:** no ar. Regressão corrigida e verificada em produção.

## O que aconteceu

O operador clicou "APLICAR MELHORIA" três vezes seguidas no agente Ellen, com
pedidos legítimos e específicos: saudar o lead, não usar travessão, apresentar-se
pelo nome. **Os três funcionaram** — medido 3/3 contra a API real.

E o terceiro quebrou uma coisa que ninguém pediu.

Para atender "sempre se apresente e pergunte em que posso ajudar", a Mestre
**apagou** uma regra que existia desde a criação do agente:

> Quando o lead iniciar com uma saudação genérica ("oi") ou com uma pergunta ampla
> ("queria entender o que vocês fazem"), não desvie — responda brevemente e já
> direcione para o contexto dele.

E depois trocou o último exemplo que ainda ensinava a responder por uma abertura
que só se apresenta. Resultado medido, mesmo lead, `"queria entender o que vocês
fazem"`:

| | antes | depois |
|---|---|---|
| respondeu a pergunta | 3/3 | **0/3** |

O lead dizia o que queria e recebia de volta *"Em que posso te ajudar?"*.

Duas versões da mesma pergunta, somadas: **6/6 antes, 2/6 depois.** Determinístico
na frase que estava escrita como exemplo no prompt, loteria fora dela — o que é
pior, não melhor: o operador não consegue prever o primeiro contato.

## Por que ninguém viu

O `_leia_me` do roteiro dizia, com todas as letras:

> expectativa sobre QUAL AÇÃO o agente deve tomar — não sobre o texto que ele
> escreve. **Texto é estilo; ação é o produto.**

Essa frase estava errada, e foi ela que abriu o buraco. Numa abertura de SDR, o
texto **é** o produto: não existe ferramenta a chamar, existe um lead decidindo se
continua a conversa.

Três defeitos empilhados:

1. **O roteiro só media ferramenta.** Nenhum caso perguntava se o agente respondeu.
2. **`avaliar` era `if/elif`.** Um caso afirmava só o PRIMEIRO critério que tivesse;
   os demais eram lidos do JSON e ignorados em silêncio. Não dava para dizer
   "não escala **E** responde o lead".
3. **O ciclo de treino rodava uma amostra por caso.** Uma amostra não distingue "a
   mudança causou isso" de "o modelo variou".

Somados, `mestre_ciclo.verificar` classificava a piora como **"corrigiu"**
(`antes_ok=False → depois_ok=True`), deixava `quebrou` vazio, e devolvia
`recomendacao: "publicar"` com o resumo *"nada quebrou"*.

**Não é só que ele deixava de sinalizar a piora. Ele contava a piora como conserto
e mandava publicar.**

## O conserto

**No roteiro** — três casos novos, e o `_leia_me` corrigido com o episódio no lugar
da crença. Os casos vêm **em par**, de propósito:

- dois que pegam a regressão (a frase copiada do prompt, e outra redação);
- um **contrapeso**: no `"oi"` seco, devolver *"em que posso te ajudar?"* é o
  comportamento CERTO, que o operador pediu. Só o negativo premiaria um agente que
  nunca se apresenta; só o positivo premiaria o que recita a abertura e nunca
  responde.

O critério **não proíbe a frase, proíbe TERMINAR com ela**. A âncora `$` do regex é
load-bearing: sem ela, a resposta que explica o serviço e se oferece para ajudar no
meio seria reprovada como regressão. Validada contra 84 respostas reais capturadas:
casou 4/4 das que ignoraram o lead e 0/12 das que responderam.

**Em `avaliar`** — todos os critérios do caso valem, e `falhou_em` diz qual
reprovou. Com vários critérios, "FALHA" sozinho manda o operador adivinhar.

**No ciclo** — os casos que **discordaram** entre antes e depois são reamostrados
até 3 vezes de cada lado, e o veredito sai por maioria. Só os que mudaram: o que
ficou igual dos dois lados não tem o que confirmar, e reamostrar tudo triplicaria a
conta. Cada linha leva `amostras` ("2/3 depois, 0/3 antes").

Maioria e **não** `all()`: exigir 3/3 apagaria o ganho real de um caso indo de 0/3
para 2/3. E não `any()`: 3/3 → 1/3 é regressão, não "seguia ok".

## A prova de que o ciclo funciona

Rodado em produção, pedindo o conserto:

1. **Primeira rodada, antes do conserto do ciclo:** corrigiu os dois casos, manteve
   o contrapeso, e recusou publicar — mas pelo motivo errado, acusando
   `nao_inventa_dado` como QUEBROU. Medido 6× de cada lado: **1/6 no atual e 1/6 no
   candidato**. Regressão fantasma de amostra única.
   *Alarme falso é pior que nenhum alarme: o operador aprende a ignorar o veredito.*
2. **Segunda rodada, com confirmação por amostra:** `recomendacao: publicar`,
   `responde_quem_ja_declarou_intencao` 0/3 → 3/3, contrapeso intacto, nada quebrou.
3. **Publicado (versão #27) e remedido por fora**, com o mesmo script do
   diagnóstico: 3/3 ignoravam a pergunta → **0/3**. Os três pedidos originais
   seguem 3/3. Zero travessão em 42 respostas.

## O que continua quebrado, e não é disto

`nao_inventa_dado` acerta ~1 em 6: o agente quase nunca escala quando perguntam por
certificação ISO 27001 / SLA. **Defeito pré-existente**, não veio destes ajustes, e
não foi consertado aqui.

## Ressalva registrada

`"em que posso te ajudar"` — a frase que o operador pediu — está literalmente em
`utils/guardrails.py:19`, na lista de frases proibidas, e o guardrail roda nos dois
motores (`ai_service.py:387`). Só dispara com `agent_type == "outbound"`, e a Ellen
é `inbound`, então hoje não morde. Se alguém marcar esse agente como outbound, o
runtime vai regenerar a abertura que ele acabou de pedir e ninguém vai entender por
quê. Decidir qual dos dois está errado.

Relacionado: [[decisoes/medir-antes-de-melhorar]] (a sonda, que media só ferramenta),
[[decisoes/mestre-melhoria-plano]] (o ciclo fechado).
