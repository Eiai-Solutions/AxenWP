"""
AgentSpec — o contrato estruturado que a IA Mestre emite.

Substitui o blob de prosa por um objeto auditável (ver
docs/wiki/decisoes/ia-mestre-portadora-do-metodo.md). Regra inegociável do passo
1: o LLM só declara INTENÇÃO — nunca IDs opacos do CRM nem flags de ativação.

Por isso o schema OMITE, por construção (não só por instrução):
- `key` do campo — derivada por código (`_slug`), não pelo LLM;
- `ghl_field_id` — resolvido por `match_ghl_field` contra o CRM real;
- `qualification_pipeline_id` / `stage_id` — resolvidos por `pick_pipeline_stage`;
- `qualification_enabled`, `is_active`, chaves/segredos — decididos por código.

O LLM não tem onde colocar essas coisas: elas não existem no schema. É a defesa
mais forte que "instruir o modelo a não fazer".
"""

from typing import Optional

from pydantic import BaseModel, Field


class QualFieldSpec(BaseModel):
    """Um dado que o agente deve coletar do lead — só INTENÇÃO."""

    label: str = Field(description="Rótulo curto do dado, como aparece para o operador. Ex.: 'Orçamento'.")
    description: str = Field(
        default="",
        description="Por que este dado importa e como reconhecê-lo na conversa. Guia o agente.",
    )
    type: str = Field(
        default="text",
        description="Tipo do dado: text | number | email | phone | date | choice.",
    )


class AgentSpec(BaseModel):
    """
    A config do agente que a Mestre projeta a partir do formulário do cliente.

    `system_prompt` é o artefato central — a prosa que a Mestre ainda autora. O
    resto é intenção estruturada que o código transforma em config segura.
    """

    system_prompt: str = Field(
        description="O prompt de sistema COMPLETO do agente, em português, pronto para uso. "
        "É a persona, as regras, o tom e o passo a passo do atendimento."
    )
    agent_name: Optional[str] = Field(
        default=None, description="Nome do agente, se fizer sentido dar um. Ex.: 'Sofia'."
    )
    channel_mode: str = Field(
        default="inbound",
        description="'inbound' (o lead procura a empresa) ou 'outbound' (o agente aborda o lead).",
    )
    tone_register: str = Field(
        default="neutro",
        description="Tom: premium | casual | support | neutro.",
    )
    wants_qualification: bool = Field(
        default=False,
        description="True se o negócio precisa QUALIFICAR leads (coletar dados e registrar no CRM). "
        "Isto é só a intenção — ligar de fato depende de o CRM ter um funil configurado.",
    )
    qualification_fields: list[QualFieldSpec] = Field(
        default_factory=list,
        description="Os dados a coletar quando wants_qualification=True. Máximo ~12; foque no essencial "
        "para não transformar a conversa em interrogatório.",
    )
    qualification_summary_prompt: Optional[str] = Field(
        default=None,
        description="Instrução de como resumir o lead qualificado para o vendedor humano, se aplicável.",
    )
    restrictions: list[str] = Field(
        default_factory=list,
        description="Coisas que o agente NÃO pode fazer/dizer (ex.: prometer preço, dar prazo). Auditoria.",
    )


# Tipos que o painel/o agente sabem renderizar — o LLM pode devolver qualquer
# string em `type`; normalizamos aqui para não confiar cegamente no modelo.
_TIPOS_VALIDOS = {"text", "number", "email", "phone", "date", "choice"}


def normalize_field_type(t: Optional[str]) -> str:
    t = (t or "").strip().lower()
    return t if t in _TIPOS_VALIDOS else "text"


def spec_fields_as_intent(spec: "AgentSpec") -> list[dict]:
    """
    Campos do Spec no formato que `build_agent_provisioning` consome como fonte:
    só `label`/`description`/`type`. `key` e `ghl_field_id` são resolvidos depois,
    por código — nunca vêm daqui.
    """
    out: list[dict] = []
    for f in spec.qualification_fields or []:
        label = (f.label or "").strip()
        if not label:
            continue
        out.append({
            "label": label,
            "description": (f.description or "").strip(),
            "type": normalize_field_type(f.type),
        })
    return out
