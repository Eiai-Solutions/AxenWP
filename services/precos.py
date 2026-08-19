"""
Preço e custo de cada chamada — o que faltava para o painel dizer a verdade.

Até 2026-08-19 `save_usage_log` aceitava `cost_usd` e NENHUM chamador passava.
O painel somava 2.650 tokens e mostrava **$0,0000**. Um zero que não distingue
"não gastei" de "não sei calcular" é pior que célula vazia: parece dado.

Três regras moram aqui.

1. **Cache é preço diferente, não desconto.** A Anthropic cobra leitura de cache
   a ~10% do input e ESCRITA a 125% (TTL 5min — o único que usamos: todo
   `cache_control` do projeto é `{"type": "ephemeral"}` sem `ttl`). Somar tudo
   como input erra dos dois lados: subestima o turno que ESCREVE o prefixo e
   superestima os ~87% de reaproveitamento que vêm depois. Os campos existiam na
   resposta da API desde sempre e eram descartados no log.

2. **Busca web é cobrada por REQUISIÇÃO, à parte dos tokens.** A entrevista da
   Mestre usa `web_search`; sem contar aqui, o gasto some da conta inteira.

3. **Modelo sem preço na tabela devolve `None`, não `0.0`.** `None` sobe até o
   painel como "não precificado" e é contado à parte, com o número de chamadas.
   Assim um provedor que ninguém tabelou aparece como buraco declarado em vez de
   virar economia imaginária.

Preços em USD por 1M tokens, tarifa de primeira parte da Anthropic.
Fonte: skill `claude-api` (tabela de 2026-06-24). **Ao adicionar modelo, confira
em https://platform.claude.com/docs/en/pricing — não chute.** Preço chutado é
pior que preço ausente, porque ausente o painel avisa.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from typing import Optional

from utils.logger import logger

# (entrada, saída) em USD por 1M tokens.
PRECOS_ANTHROPIC: dict[str, tuple[float, float]] = {
    "claude-fable-5": (10.0, 50.0),
    "claude-mythos-5": (10.0, 50.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

# Promoção de lançamento do Sonnet 5, o modelo default do projeto inteiro
# (agente, Mestre e sonda). Fica como regra com data em vez de número fixo
# porque as duas alternativas erram: $3/$15 superestima 50% do gasto de HOJE,
# $2/$10 subestima a partir de setembro. Vence sozinha, sem ninguém lembrar.
PROMOCOES: dict[str, tuple[tuple[float, float], date]] = {
    "claude-sonnet-5": ((2.0, 10.0), date(2026, 8, 31)),
}

# TTS do Fish Audio, em USD por 1M de CARACTERES.
#
# MEDIDO, não copiado de página de preço: li o crédito da conta antes e depois de
# uma síntese, esperando a cobrança assentar (ela não é instantânea — sem esperar,
# o gasto de uma chamada é atribuído à seguinte, e foi assim que a primeira
# medição saiu errada). 4.000 caracteres em s2-pro derrubaram o crédito em
# exatamente $0,12.
#
# `s1` fica DE FORA de propósito: 8.000 caracteres não moveram o crédito nesta
# conta, mas "não cobrou aqui" não é o mesmo que "é grátis para todo mundo" —
# plano diferente cobra diferente. Sem preço, o painel diz "sem preço"; com 0.0
# ele diria "de graça", que é o defeito que este módulo existe para não repetir.
# Quem souber a própria tarifa preenche via PRECOS_EXTRA_JSON.
PRECOS_FISHAUDIO_POR_1M_CARACTERES: dict[str, float] = {
    "s2-pro": 30.0,
}

MULT_CACHE_LEITURA = 0.10   # ~10% do preço de entrada
MULT_CACHE_ESCRITA = 1.25   # 125% — TTL 5min (o de 1h seria 2.00; não usamos)

# Ferramenta server-side `web_search`: USD por 1.000 requisições.
PRECO_BUSCA_WEB_POR_1K = 10.0

_SUFIXO_DATA = re.compile(r"-\d{8}$")


def _normalizar(modelo: Optional[str]) -> str:
    """`claude-haiku-4-5-20251001` → `claude-haiku-4-5`."""
    return _SUFIXO_DATA.sub("", (modelo or "").strip().lower())


def _precos_extra() -> dict:
    """
    Tabela de fora, para provedor que o código não tabela (OpenRouter, ElevenLabs,
    Groq) ou preço contratado diferente do público. Assim o operador corrige a
    conta sem esperar deploy.

        PRECOS_EXTRA_JSON={"openrouter:openai/gpt-4o": {"entrada": 2.5, "saida": 10},
                           "elevenlabs:*": {"caractere": 300}}

    Valores por 1M unidades (tokens para entrada/saída, caracteres para TTS).
    A chave é `servico:modelo`; `servico:*` casa qualquer modelo do serviço.
    """
    bruto = (os.getenv("PRECOS_EXTRA_JSON") or "").strip()
    if not bruto:
        return {}
    try:
        d = json.loads(bruto)
        return d if isinstance(d, dict) else {}
    except Exception as e:
        logger.warning(f"[PRECOS] PRECOS_EXTRA_JSON ilegível ({e}); ignorado.")
        return {}


def _tarifa(
    service: Optional[str], model: Optional[str], quando: Optional[date]
) -> Optional[tuple[float, float, float]]:
    """(entrada, saída, caractere) por 1M unidades — ou None se não há preço."""
    svc = (service or "").strip().lower()
    nome = _normalizar(model)

    extra = _precos_extra()
    achado = extra.get(f"{svc}:{nome}") or extra.get(f"{svc}:*")
    if isinstance(achado, dict):
        return (
            float(achado.get("entrada") or 0.0),
            float(achado.get("saida") or 0.0),
            float(achado.get("caractere") or 0.0),
        )

    if svc == "fishaudio":
        por_1m = PRECOS_FISHAUDIO_POR_1M_CARACTERES.get(nome)
        return None if por_1m is None else (0.0, 0.0, por_1m)

    if svc != "anthropic":
        return None

    promo = PROMOCOES.get(nome)
    if promo and (quando or date.today()) <= promo[1]:
        return (promo[0][0], promo[0][1], 0.0)

    par = PRECOS_ANTHROPIC.get(nome)
    return None if par is None else (par[0], par[1], 0.0)


def custo(
    service: str,
    model: Optional[str] = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    characters: int = 0,
    buscas_web: int = 0,
    quando: Optional[date] = None,
) -> Optional[float]:
    """
    Custo em USD da chamada, ou **None** quando não há preço tabelado.

    `None` não é erro: é a diferença entre "de graça" e "não sei". Quem grava
    persiste `None` e o painel conta essas chamadas numa linha própria.
    """
    tarifa = _tarifa(service, model, quando)

    # A busca web é preço fixo da Anthropic, independente do modelo — ela é
    # conhecida mesmo quando o modelo não está na tabela.
    custo_busca = (buscas_web or 0) * PRECO_BUSCA_WEB_POR_1K / 1_000

    if tarifa is None:
        return round(custo_busca, 8) if buscas_web else None

    entrada, saida, caractere = tarifa
    total = (
        (input_tokens or 0) * entrada
        + (cache_read_tokens or 0) * entrada * MULT_CACHE_LEITURA
        + (cache_write_tokens or 0) * entrada * MULT_CACHE_ESCRITA
        + (output_tokens or 0) * saida
        + (characters or 0) * caractere
    ) / 1_000_000
    return round(total + custo_busca, 8)


def tem_preco(service: str, model: Optional[str] = None) -> bool:
    """Para a tela poder avisar antes de gastar, sem inventar número."""
    return _tarifa(service, model, None) is not None
