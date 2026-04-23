"""
Runtime guardrails para respostas do agente.

Filtros em camadas:
1. Forbidden phrases por modo (outbound)
2. Sentiment detection → escalação
3. Handoff context para passar contexto ao humano
"""

import re
from typing import Literal

Mode = Literal["inbound", "outbound"]


# Frases proibidas no modo OUTBOUND — se a resposta contém, regenera ou remove
OUTBOUND_FORBIDDEN_PATTERNS = [
    r"\bcomo\s+posso\s+(?:te\s+|lhe\s+)?ajudar\b",
    r"\bem\s+que\s+posso\s+(?:te\s+|lhe\s+)?ajudar\b",
    r"\btudo\s+bem\s*\??\s*$",
    r"\bestou\s+(?:aqui|à\s+disposição)\s+para\s+ajudar\b",
    r"\bem\s+que\s+posso\s+ser\s+útil\b",
    r"\bposso\s+(?:te\s+)?auxiliar\b",
    r"\bcomo\s+podemos\s+(?:te\s+|lhe\s+)?ajudar\b",
]

# Palavras/frases que indicam frustração/sentimento negativo
NEGATIVE_SENTIMENT_PATTERNS = [
    r"\b(?:que\s+)?(?:merda|porra|droga|bosta)\b",
    r"\b(?:não|nao)\s+(?:funciona|tá\s+funcionando|está\s+funcionando)\b",
    r"\b(?:isso|isto)\s+(?:é|e)\s+(?:ruim|péssimo|horrível|horrivel|terrível|terrivel)\b",
    r"\b(?:péssimo|pessimo|horrível|horrivel|terrível|terrivel|inaceitável|inaceitavel)\b",
    r"\bestou\s+(?:frustrado|irritado|furioso|puto|pistola)\b",
    r"\b(?:quero|preciso)\s+(?:falar|conversar)\s+com\s+(?:um\s+)?humano\b",
    r"\b(?:atendente|pessoa|humano)\s+de\s+verdade\b",
    r"\bcancelar?\s+(?:minha\s+)?(?:assinatura|conta|compra)\b",
    r"\breclama(?:ção|cao)\b",
    r"\bprocon\b",
    r"\bprocessar\b",
]

# Explicit escalation triggers (user asks for human)
EXPLICIT_ESCALATION_PATTERNS = [
    r"\bfalar?\s+com\s+(?:um\s+)?(?:humano|atendente|pessoa|vendedor|consultor)\b",
    r"\bpassar?\s+(?:para|pra)\s+(?:um\s+)?(?:humano|atendente)\b",
    r"\bbot\s+não\s+(?:tá|está)\s+(?:entendendo|ajudando)\b",
]


def contains_forbidden_phrase(text: str, mode: Mode) -> str | None:
    """Retorna o padrão encontrado se a resposta contém frase proibida para o modo."""
    if mode != "outbound":
        return None
    text_lower = text.lower()
    for pattern in OUTBOUND_FORBIDDEN_PATTERNS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            return pattern
    return None


# Regex para capturar qualquer emoji (blocos Unicode comuns de emoji)
_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "\U00002600-\U000026FF"  # misc symbols
    "\U00002700-\U000027BF"  # dingbats
    "]+",
    flags=re.UNICODE,
)


def strip_emojis(text: str) -> str:
    """Remove todos os emojis da mensagem e limpa espaços extras."""
    cleaned = _EMOJI_RE.sub("", text)
    # Normaliza múltiplos espaços/quebras que sobraram
    cleaned = re.sub(r" +", " ", cleaned)
    cleaned = re.sub(r" ?\n ?", "\n", cleaned)
    return cleaned.strip()


def detect_negative_sentiment(text: str) -> bool:
    """True se a mensagem contém sinal claro de frustração/sentimento negativo."""
    text_lower = text.lower()
    for pattern in NEGATIVE_SENTIMENT_PATTERNS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            return True
    return False


def detect_explicit_escalation(text: str) -> bool:
    """True se o usuário pediu explicitamente um humano."""
    text_lower = text.lower()
    for pattern in EXPLICIT_ESCALATION_PATTERNS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            return True
    return False


def should_escalate(user_message: str) -> tuple[bool, str | None]:
    """
    Decide se a conversa deve ser escalada para humano.
    Retorna (should_escalate, reason).
    """
    if detect_explicit_escalation(user_message):
        return True, "explicit_request"
    if detect_negative_sentiment(user_message):
        return True, "negative_sentiment"
    return False, None


def build_handoff_context(
    company_name: str,
    contact_name: str | None,
    contact_phone: str,
    reason: str,
    last_messages: list[dict],
    qualified_data: dict | None = None,
) -> str:
    """
    Gera um resumo estruturado para o atendente humano.

    Args:
        company_name: nome da empresa (tenant)
        contact_name: nome do contato (se conhecido)
        contact_phone: telefone do contato
        reason: motivo da escalação (explicit_request, negative_sentiment, qualified)
        last_messages: últimas mensagens da conversa [{role, content}, ...]
        qualified_data: dados já coletados se for lead qualificado
    """
    reason_labels = {
        "explicit_request": "🙋 Cliente pediu para falar com humano",
        "negative_sentiment": "⚠️ Sentimento negativo detectado — intervenção recomendada",
        "qualified": "✅ Lead qualificado — pronto para fechar",
        "fallback": "🔁 Agente não conseguiu resolver (3+ tentativas)",
    }

    lines = [
        f"🔔 *ATENDIMENTO TRANSFERIDO* — {company_name}",
        "",
        f"*Motivo:* {reason_labels.get(reason, reason)}",
        f"*Contato:* {contact_name or 'Não identificado'} ({contact_phone})",
    ]

    if qualified_data:
        lines.append("")
        lines.append("*Dados coletados:*")
        for k, v in qualified_data.items():
            lines.append(f"  • {k}: {v}")

    if last_messages:
        lines.append("")
        lines.append("*Últimas mensagens:*")
        for m in last_messages[-6:]:
            role = "Cliente" if m.get("role") == "human" else "Agente"
            content = (m.get("content") or "")[:200]
            lines.append(f"  [{role}] {content}")

    return "\n".join(lines)
