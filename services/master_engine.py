"""
IA Mestre via Anthropic — emite um AgentSpec estruturado, não prosa.

Caller fino e próprio (não reusa o `ClaudeAgentEngine`, que é loop de tool-use e
não sabe devolver saída estruturada — ver a decisão registrada). Single-turn com
`messages.parse`: o SDK transforma o Pydantic `AgentSpec` em json_schema e valida
a resposta, então o retorno já é o objeto tipado.

Sem prompt caching de propósito: o prefixo da Mestre fica abaixo do mínimo do
modelo e o padrão de chamada é esparso (um onboarding por vez) — cachear seria
net-negativo. `messages.parse` não envia `cache_control` e passamos `system` como
string pura.

Fail-closed: qualquer falha (API, JSON truncado, schema inválido) levanta exceção;
quem chama mantém a submissão `pending` e não cria agente meia-boca.
"""

import os
from typing import Optional

from utils.agent_spec import AgentSpec
from utils.logger import logger
from utils.master_prompt import build_company_context

# A Mestre é admin-scoped (não é por-agente), então o modelo vem do ambiente com
# um default sensato. Qualidade importa mais que custo aqui (a chamada é rara).
DEFAULT_MASTER_MODEL = "claude-sonnet-5"
_MAX_TOKENS = 8000  # o system_prompt do agente pode ser longo; folga para não truncar o JSON

# O método (a "Fórmula") reaproveita o MASTER_SYSTEM_PROMPT existente, só troca o
# CONTRATO DE SAÍDA: em vez de "escreva o prompt", "preencha o AgentSpec". O
# system_prompt do agente vai DENTRO do campo `system_prompt` do Spec.
_SPEC_INSTRUCTION = """
Você vai preencher um AgentSpec estruturado para este cliente.

REGRAS DO CONTRATO:
- `system_prompt`: escreva o prompt de sistema COMPLETO do agente, em português,
  denso e pronto para uso (persona, tom, regras, passo a passo do atendimento).
  É o mesmo trabalho de sempre — só que ele vai dentro deste campo.
- `wants_qualification`: true se o negócio precisa coletar dados do lead e
  registrar no CRM (SDR, geração de leads). false para suporte puro.
- `qualification_fields`: se wants_qualification, liste os DADOS a coletar — cada
  um com um `label` curto (o nome do dado, ex.: "Orçamento", "Empresa"), uma
  `description` (por que importa) e um `type`. Foque no essencial (máx ~8), para a
  conversa não virar interrogatório. NÃO invente identificadores técnicos — só o
  dado em linguagem natural.
- `restrictions`: o que o agente NÃO pode fazer (prometer preço, dar prazo, etc.).
- Escolha `channel_mode` e `register` conforme o contexto do cliente.

Não se preocupe com IDs de CRM, funis, ativação ou chaves — isso é resolvido pelo
sistema depois. Você só descreve a INTENÇÃO do agente.
""".strip()


def _resolve_master_key() -> Optional[str]:
    """Chave da Mestre: admin global → env. (Admin-scoped: sem agent_data.)"""
    try:
        from data.database import SessionLocal
        from data.models import SystemSettings

        db = SessionLocal()
        try:
            s = db.query(SystemSettings).first()
            if s and (getattr(s, "admin_anthropic_key", None) or "").strip():
                return s.admin_anthropic_key.strip()
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"[MESTRE] Falha ao ler admin_anthropic_key: {e}")
    return (os.getenv("ANTHROPIC_API_KEY") or "").strip() or None


def _read_settings() -> tuple[str, Optional[str]]:
    """(master_engine, admin_anthropic_model) do banco — vazio se indisponível."""
    try:
        from data.database import SessionLocal
        from data.models import SystemSettings

        db = SessionLocal()
        try:
            s = db.query(SystemSettings).first()
            if s:
                return (getattr(s, "master_engine", "openrouter") or "openrouter"), getattr(s, "admin_anthropic_model", None)
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"[MESTRE] Falha ao ler master_engine: {e}")
    return "openrouter", None


def is_configured() -> bool:
    """
    A Mestre-Anthropic (structured) é o caminho quando há chave E o motor escolhido
    é 'anthropic'. O toggle é DELIBERADO e independente do motor dos AGENTES: a
    mesma `admin_anthropic_key` liga o motor Claude de um agente, e sem este gate
    a Mestre de TODOS os tenants trocaria de OpenRouter-prosa para AgentSpec no
    instante em que a chave aparecesse — mudança de frota sem ninguém pedir.

    Fonte do toggle: o painel (System Settings → IA Mestre → Motor). O env
    `MASTER_ENGINE=anthropic` continua valendo como override de operação.
    """
    if not _resolve_master_key():
        return False
    env = (os.getenv("MASTER_ENGINE") or "").strip().lower()
    if env in ("anthropic", "spec", "structured"):
        return True
    if env in ("openrouter", "legacy"):
        return False
    return _read_settings()[0].strip().lower() == "anthropic"


def _build_user_message(form_data: dict) -> str:
    return f"{_SPEC_INSTRUCTION}\n\n---\n\nCONTEXTO DO CLIENTE:\n\n{build_company_context(form_data)}"


async def generate_agent_spec(form_data: dict) -> AgentSpec:
    """
    Roda a Mestre e devolve o AgentSpec validado. Levanta em qualquer falha —
    o chamador trata como fail-closed (submissão fica pending).
    """
    key = _resolve_master_key()
    if not key:
        raise RuntimeError("IA Mestre (Anthropic) não configurada — falta a chave em System Settings.")

    from anthropic import AsyncAnthropic
    from utils.master_prompt import MASTER_SYSTEM_PROMPT

    model = (
        (os.getenv("MASTER_ANTHROPIC_MODEL") or "").strip()
        or (_read_settings()[1] or "").strip()
        or DEFAULT_MASTER_MODEL
    )
    client = AsyncAnthropic(api_key=key, timeout=120.0, max_retries=2)

    resp = await client.messages.parse(
        model=model,
        max_tokens=_MAX_TOKENS,
        system=MASTER_SYSTEM_PROMPT,  # string pura → sem cache_control
        messages=[{"role": "user", "content": _build_user_message(form_data)}],
        output_format=AgentSpec,
    )

    spec = getattr(resp, "parsed_output", None)
    if spec is None:
        raise RuntimeError(f"Mestre devolveu Spec vazio (stop={getattr(resp, 'stop_reason', '?')}).")
    if not (spec.system_prompt or "").strip():
        raise RuntimeError("Mestre devolveu Spec sem system_prompt.")
    logger.info(
        f"[MESTRE] Spec gerado ({model}): qualificação={spec.wants_qualification}, "
        f"campos={len(spec.qualification_fields)}, prompt={len(spec.system_prompt)} chars"
    )
    return spec
