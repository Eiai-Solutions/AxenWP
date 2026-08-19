"""Registro de uso de APIs externas (Anthropic, OpenRouter, Groq, ElevenLabs) por tenant."""

import asyncio
from typing import Any, Optional

from data.database import SessionLocal
from data.models import UsageLog
from services.precos import custo
from utils.logger import logger

ATENDIMENTO = "atendimento"
MESTRE = "mestre"


def save_usage_log(
    location_id: str,
    service: str,
    model: Optional[str] = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    characters: int = 0,
    cost_usd: Optional[float] = None,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    buscas_web: int = 0,
    origem: str = ATENDIMENTO,
) -> None:
    """
    Persiste um registro de uso. Sync por design — chamar via asyncio.to_thread().

    service: anthropic | openrouter | groq | elevenlabs
    origem : atendimento (agente falando com lead) | mestre (gerar/melhorar/testar)

    `cost_usd=None` (o default) manda CALCULAR pela tabela de preços. Era o buraco:
    o parâmetro existia com default `0.0` e nenhum chamador passava valor, então
    toda linha nascia custando zero e o painel somava zero para sempre. Passar um
    número explícito continua valendo — inclusive `0.0`, que aí quer dizer "de
    graça mesmo". Sem preço tabelado grava NULL, que o painel mostra como
    "não precificado" em vez de fingir gratuidade.
    """
    if cost_usd is None:
        cost_usd = custo(
            service=service,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            characters=characters,
            buscas_web=buscas_web,
        )

    db = SessionLocal()
    try:
        log = UsageLog(
            location_id=location_id,
            service=service,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            characters=characters,
            buscas_web=buscas_web,
            origem=origem or ATENDIMENTO,
            cost_usd=cost_usd,
        )
        db.add(log)
        db.commit()
    except Exception as e:
        logger.error(f"Erro ao salvar usage log: {e}")
        db.rollback()
    finally:
        db.close()


def extrair_uso(usage: Any) -> dict:
    """
    Lê o `usage` de uma resposta Anthropic (objeto do SDK ou dict) num formato só.

    Existe para os quatro caminhos da Mestre não repetirem a mesma leitura de
    atributo — e para `server_tool_use.web_search_requests` não ser esquecido em
    três deles, que foi como a busca web ficou invisível na conta.
    """
    if not usage:
        return {}

    def _ler(obj, nome, padrao=0):
        if isinstance(obj, dict):
            return obj.get(nome, padrao) or padrao
        return getattr(obj, nome, padrao) or padrao

    servidor = _ler(usage, "server_tool_use", None)

    return {
        "input_tokens": _ler(usage, "input_tokens"),
        "output_tokens": _ler(usage, "output_tokens"),
        "cache_read_tokens": _ler(usage, "cache_read_input_tokens"),
        "cache_write_tokens": _ler(usage, "cache_creation_input_tokens"),
        "buscas_web": _ler(servidor, "web_search_requests") if servidor else 0,
    }


async def registrar_gasto_mestre(
    location_id: Optional[str], model: Optional[str], usage: Any
) -> None:
    """
    Grava um turno da IA Mestre como `origem="mestre"`. Nunca levanta.

    A Mestre gasta a chave Anthropic do admin em quatro caminhos (gerar spec,
    entrevista, ciclo de treino, sonda) e NENHUM deles registrava nada — o painel
    só via o atendimento. Falhar aqui não pode derrubar o trabalho que já foi
    pago e entregue, então todo erro vira warning.
    """
    if not location_id:
        # Sem tenant não há onde pendurar (a FK exige `tenants.location_id`).
        # Acontece em teste e em fluxo anônimo; registrar o silêncio é melhor que
        # gravar num tenant chutado.
        logger.debug("[CUSTO-MESTRE] sem location_id; gasto não registrado.")
        return
    try:
        dados = extrair_uso(usage)
        if not dados:
            return
        await asyncio.to_thread(
            save_usage_log,
            location_id=location_id,
            service="anthropic",
            model=model,
            origem=MESTRE,
            **dados,
        )
    except Exception as e:
        logger.warning(f"[CUSTO-MESTRE] falha ao registrar gasto: {e}")
