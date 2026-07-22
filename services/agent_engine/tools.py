"""
Specs das ferramentas expostas ao modelo (motor Claude / tool-use).

Só o CONTRATO (o que o modelo vê e decide chamar). O EFEITO de cada tool é
executado pelo `tool_dispatch` que o pipeline injeta no AgentContext — os efeitos
colaterais (criar opportunity no GHL, pausar a IA) ficam FORA do engine, reusando
`handle_qualification`/kill-switch que já existem e já são idempotentes.

Estas duas tools dão PARIDADE com o comportamento de hoje, virando ações
explícitas em vez de:
- qualificação por marcador de texto `[QUALIFIED_DATA]` + regex;
- escalação que hoje é output morto (`should_escalate` não é consumido).

O conjunto é ESTÁVEL entre requests (pré-requisito do prompt caching) e por-agente:
`build_tool_specs` só inclui a tool de qualificação quando o agente a habilita.
"""

from __future__ import annotations

from typing import Optional

from services.agent_engine.base import ToolSpec

QUALIFY = "register_qualified_lead"
ESCALATE = "escalate_to_human"


def _qualify_spec(fields: list) -> ToolSpec:
    """
    Campos de coleta viram propriedades tipadas — o modelo preenche o que apurou
    na conversa. Os `auto` (derivados do sistema, ex. telefone) NÃO entram aqui;
    o handler os resolve. A description é parte do prompt: diz QUANDO chamar.
    """
    props = {}
    obrig = []
    for f in fields or []:
        key = (f.get("key") or "").strip()
        if not key or f.get("auto"):
            continue
        props[key] = {
            "type": "string",
            "description": f.get("label") or key,
        }
        obrig.append(key)
    return ToolSpec(
        name=QUALIFY,
        description=(
            "Registra o lead como QUALIFICADO no CRM. Chame SOMENTE quando tiver "
            "coletado, de forma explícita na conversa, TODOS os campos exigidos — "
            "nunca invente nem deduza um campo que o lead não informou. A ação é "
            "idempotente: não cria duplicata se o lead já foi qualificado."
        ),
        input_schema={
            "type": "object",
            "properties": props,
            "required": obrig,
        },
    )


_ESCALATE_SPEC = ToolSpec(
    name=ESCALATE,
    description=(
        "Transfere a conversa para um atendente humano e PAUSA a IA nesta "
        "conversa. Use quando: o lead pede explicitamente falar com humano; "
        "demonstra forte frustração; ou você precisa de um dado que não está "
        "na sua base e não pode inventar (fail-closed — prefira escalar a "
        "afirmar algo que não verificou)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "motivo": {
                "type": "string",
                "description": "Motivo curto do repasse, para o humano assumir com contexto.",
            }
        },
        "required": ["motivo"],
    },
)


def build_tool_specs(agent_config) -> list:
    """
    Tools do agente, na ordem determinística (estável para o cache).
    `escalate_to_human` sempre presente; `register_qualified_lead` só quando o
    agente tem qualificação habilitada e campos definidos.
    """
    specs: list[ToolSpec] = [_ESCALATE_SPEC]
    if getattr(agent_config, "qualification_enabled", False):
        campos = getattr(agent_config, "qualification_fields", None) or []
        if any((c.get("key") and not c.get("auto")) for c in campos):
            specs.append(_qualify_spec(campos))
    return specs
