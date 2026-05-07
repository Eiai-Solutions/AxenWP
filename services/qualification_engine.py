"""
Lógica de qualificação de leads:
- Detecção do marcador [QUALIFIED_DATA] na resposta do LLM
- Cache de progresso por sessão (campos coletados parcialmente)
- Geração de resumo da conversa para o closer humano
- Verificação se um lead já foi qualificado (evita reprocessar)
"""

import asyncio
import json
import re
from typing import List, Optional

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from data.database import SessionLocal
from data.models import QualifiedLead
from services.usage_logger import save_usage_log
from utils.logger import logger


_QUALIFICATION_MARKER_RE = re.compile(
    r"\[QUALIFIED_DATA\]\s*(\{.*?\})\s*\[/QUALIFIED_DATA\]",
    re.DOTALL,
)

# Cache em memória do progresso de qualificação por sessão.
# Chave: session_id (location_id_phone), Valor: {field_key: value}
qual_progress_cache: dict[str, dict] = {}


_DEFAULT_SUMMARY_PROMPT = """Voce e um assistente que gera resumos de conversas de qualificacao de leads para closers de vendas.

Analise a conversa abaixo e gere um resumo breve contendo:
1. Interesse principal do lead
2. Dados coletados durante a conversa
3. Pontos importantes mencionados
4. Proximos passos recomendados para o closer

Seja direto e objetivo. Maximo 200 palavras. Responda em portugues."""


def is_already_qualified_sync(location_id: str, phone: str) -> bool:
    """True se o lead já foi qualificado (sync — chamar via to_thread)."""
    db = SessionLocal()
    try:
        exists = (
            db.query(QualifiedLead)
            .filter(
                QualifiedLead.location_id == location_id,
                QualifiedLead.phone == phone,
            )
            .first()
        )
        return exists is not None
    finally:
        db.close()


def extract_qualification_data(
    ai_text: str,
    qualification_fields: list[dict],
    session_id: str = "",
) -> tuple[str, Optional[dict]]:
    """
    Procura o marcador [QUALIFIED_DATA] na resposta. Retorna (texto_limpo, dados ou None).
    - Parcial: armazena no cache, retorna (clean_text, None)
    - Completo: retorna (clean_text, dict_completo) — pronto para disparar qualificação
    """
    match = _QUALIFICATION_MARKER_RE.search(ai_text)
    clean_text = _QUALIFICATION_MARKER_RE.sub("", ai_text).strip()
    if not match:
        logger.debug(
            f"Qualificação: marcador não encontrado. Resposta: {ai_text[:150]}"
        )
        return ai_text, None

    try:
        data = json.loads(match.group(1))
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(f"Falha ao parsear JSON de qualificação: {e}")
        return clean_text, None

    all_keys = {f["key"] for f in qualification_fields}
    collect_keys = {f["key"] for f in qualification_fields if not f.get("auto")}
    valid_data = {k: v for k, v in data.items() if k in all_keys and v}

    if valid_data and session_id:
        qual_progress_cache[session_id] = valid_data
        logger.info(
            f"Progresso de qualificação atualizado [{session_id}]: {list(valid_data.keys())}"
        )

    missing_collect = collect_keys - set(valid_data.keys())
    if missing_collect:
        logger.info(f"Qualificação parcial. Faltam: {missing_collect}")
        return clean_text, None

    logger.info(f"Lead qualificado! Dados: {valid_data}")
    return clean_text, valid_data


async def generate_summary(
    llm,
    past_messages: List[BaseMessage],
    qualified_data: dict,
    location_id: str,
    model: str,
    custom_prompt: Optional[str] = None,
) -> str:
    """Gera um resumo da conversa para o closer usando o LLM do agente."""
    if llm is None:
        return ""

    conversation_lines = []
    for msg in past_messages:
        role = "Lead" if isinstance(msg, HumanMessage) else "Agente"
        conversation_lines.append(f"{role}: {msg.content}")
    conversation_text = "\n".join(conversation_lines)

    summary_prompt = custom_prompt or _DEFAULT_SUMMARY_PROMPT
    dados_str = json.dumps(qualified_data, ensure_ascii=False, indent=2)
    user_content = (
        f"Dados coletados:\n{dados_str}\n\nConversa completa:\n{conversation_text}"
    )

    try:
        response = await llm.ainvoke([
            SystemMessage(content=summary_prompt),
            HumanMessage(content=user_content),
        ])
        summary = response.content

        try:
            usage = getattr(response, "usage_metadata", None) or {}
            if isinstance(usage, dict):
                in_tok = usage.get("input_tokens", 0)
                out_tok = usage.get("output_tokens", 0)
            else:
                in_tok = getattr(usage, "input_tokens", 0)
                out_tok = getattr(usage, "output_tokens", 0)
            await asyncio.to_thread(
                save_usage_log,
                location_id=location_id,
                service="openrouter",
                model=model,
                input_tokens=in_tok,
                output_tokens=out_tok,
            )
        except Exception as e_log:
            logger.warning(f"Falha usage log do resumo: {e_log}")

        logger.info(f"Resumo de qualificação gerado ({len(summary)} chars)")
        return summary
    except Exception as e:
        logger.error(f"Erro ao gerar resumo: {e}")
        return ""
