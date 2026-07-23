"""
Provisionamento de agente a partir do formulário de onboarding.

O problema que este módulo resolve: a IA Mestre gera o **prompt**, mas o agente
nascia preenchendo 1 de 35 colunas — sem qualificação, sem campos, sem pipeline.
Resultado: `build_tool_specs` só entregava `escalate_to_human`, e o agente era
estruturalmente incapaz de qualificar (ver docs/wiki/decisoes/ia-mestre-portadora-do-metodo.md).

Aqui montamos o resto da config a partir do que o cliente respondeu + do que o CRM
daquele tenant realmente tem. Duas regras que valem para tudo abaixo:

1. **Fail-closed.** Sem token/CRM ou com dado ambíguo, a qualificação fica
   DESLIGADA e o motivo é reportado — nunca "meio ligada". Um agente que promete
   qualificar e não registra no CRM é pior que um que não promete.
2. **Determinístico.** As leituras do CRM têm zero graus de liberdade (sempre as
   mesmas duas, com o mesmo argumento), então são pré-busca em Python — não
   tool-use. Isso mantém o fail-closed como invariante de código, e não como
   instrução de prompt que o modelo pode desobedecer.
"""

import re
import unicodedata
from typing import Any, Optional

from auth.token_manager import token_manager
from services.ghl_service import ghl_service
from utils.logger import logger

# Marcadores de lista que o cliente costuma digitar antes da pergunta.
_BULLET = re.compile(r"^\s*(?:[-*•>]|\d+\s*[.)\-]|[a-zA-Z]\s*[.)])\s*")
_NAO_ALNUM = re.compile(r"[^a-z0-9]+")
_MAX_CAMPOS = 12  # além disso o agente vira interrogatório, não conversa


def _slug(texto: str) -> str:
    """Chave estável e ASCII para o campo — vira propriedade do input_schema da tool."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    s = _NAO_ALNUM.sub("_", sem_acento.lower()).strip("_")
    return s[:40].strip("_")


def derive_qualification_fields(questions_text: Optional[str]) -> list[dict]:
    """
    Converte as perguntas de qualificação (texto livre do formulário) nos campos
    estruturados que o agente coleta: `[{label, key}]`.

    Esta ponte não existia — `qualification_questions` era escrito no formulário e
    nunca virava `qualification_fields`, então nenhum agente gerado conseguia
    qualificar. O `ghl_field_id` é preenchido depois, só quando há casamento
    confiante com um campo real do CRM.

    Aceita o que o cliente realmente digita: uma por linha, numeradas, com
    bullets, ou várias perguntas na mesma linha separadas por `?` / `;`.
    """
    if not questions_text or not questions_text.strip():
        return []

    brutos: list[str] = []
    for linha in questions_text.splitlines():
        linha = _BULLET.sub("", linha).strip()
        if not linha:
            continue
        # Várias perguntas na mesma linha: "Qual o nome? Qual a empresa?"
        if linha.count("?") > 1:
            brutos.extend(p for p in (x.strip() for x in linha.split("?")) if p)
        elif ";" in linha:
            brutos.extend(p for p in (x.strip() for x in linha.split(";")) if p)
        else:
            brutos.append(linha)

    entradas = []
    for bruto in brutos:
        label = bruto.rstrip("?").strip()
        # Cabeçalho ("Perguntas de qualificação:") não é pergunta. Se virasse
        # campo, seria um campo OBRIGATÓRIO que o lead nunca pode responder — e a
        # qualificação nunca completaria (`_qualification_complete` exige todos).
        if not label or label.endswith(":"):
            continue
        entradas.append({"label": label})
    return _com_chaves(entradas)


def _com_chaves(entradas: list[dict]) -> list[dict]:
    """
    Atribui `key` (slug) a cada campo e deduplica — o ponto único onde a chave
    nasce, seja a fonte o parser (operador) ou o AgentSpec (Mestre). Preserva
    `description`/`type` quando vierem (o Spec os traz; o parser não).
    """
    campos: list[dict] = []
    vistos: set[str] = set()
    for e in entradas:
        label = (e.get("label") or "").strip()
        if not label or label.endswith(":"):
            continue
        chave = _slug(label)
        if not chave or chave in vistos:  # chaves iguais colidiriam no input_schema da tool
            continue
        vistos.add(chave)
        campo = {"label": label, "key": chave}
        if e.get("description"):
            campo["description"] = e["description"]
        if e.get("type"):
            campo["type"] = e["type"]
        campos.append(campo)
        if len(campos) >= _MAX_CAMPOS:
            logger.info(f"[PROVISION] Teto de {_MAX_CAMPOS} campos de qualificação atingido; resto ignorado.")
            break
    return campos


async def fetch_crm_catalog(location_id: str) -> dict:
    """
    Pré-busca o que o CRM daquele tenant tem: pipelines (com stages) e custom fields.

    Duas leituras, sempre as mesmas, sempre com o mesmo argumento — por isso é
    código, não tool. Devolve `ok=False` com motivo quando o CRM não está
    disponível; quem chama trata como fail-closed.
    """
    tenant = token_manager.get_tenant(location_id)
    if not tenant:
        return {"ok": False, "error": "Tenant não encontrado."}

    token = await token_manager.get_valid_token(location_id)
    if not token:
        return {"ok": False, "error": "Sem token do CRM — conecte o CRM na instância."}

    try:
        pipelines_res = await ghl_service.get_pipelines(location_id)
        fields_res = await ghl_service.get_custom_fields(location_id, model="all")
    except Exception as e:
        logger.error(f"[PROVISION] Falha ao ler catálogo do CRM de {location_id}: {e}")
        return {"ok": False, "error": "Falha ao consultar o CRM."}

    if pipelines_res.get("error"):
        return {"ok": False, "error": pipelines_res.get("message") or "Erro ao listar pipelines."}

    return {
        "ok": True,
        "pipelines": pipelines_res.get("pipelines") or [],
        # Custom fields são opcionais: sem eles a qualificação ainda funciona,
        # só não mapeia para campos do CRM.
        "fields": (fields_res.get("fields") or []) if not fields_res.get("error") else [],
    }


def _norm(s: Any) -> str:
    txt = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return _NAO_ALNUM.sub(" ", txt.lower()).strip()


# Aberturas de pergunta em PT-BR. O campo no CRM se chama "Orçamento", mas o
# cliente escreve "Qual é o seu orçamento?" — sem descascar isso, o casamento
# exato nunca acontece e nenhum dado sobe para o CRM.
_ABERTURAS = re.compile(
    r"^(qual|quais|quanto|quantos|quantas|como|onde|quando|me diga|informe|digite)"
    # artigos/possessivos podem vir encadeados ("qual é o seu orçamento")
    r"(\s+(e|eh|o|a|os|as|um|uma|seu|sua|seus|suas|teu|tua|do|da|de))*\s+"
)
_CAUDAS = re.compile(r"\s+(da empresa|do cliente|aproximadamente|previsto|estimado)$")


def _variantes(label: str) -> list[str]:
    """Formas normalizadas da pergunta, da mais literal à mais 'substantivada'."""
    base = _norm(label)
    if not base:
        return []
    out = [base]
    descascado = _ABERTURAS.sub("", base).strip()
    if descascado and descascado != base:
        out.append(descascado)
        sem_cauda = _CAUDAS.sub("", descascado).strip()
        if sem_cauda and sem_cauda != descascado:
            out.append(sem_cauda)
    return out


def match_ghl_field(label: str, custom_fields: list) -> Optional[str]:
    """
    Id do campo do CRM que corresponde à pergunta — só em casamento CONFIANTE.

    Casar por aproximação colocaria a resposta do lead no campo errado do CRM, que
    é pior que não mapear: sem `ghl_field_id` o dado ainda é gravado no lead
    qualificado (`qualification_handler`), só não sobe para o custom field.
    """
    for alvo in _variantes(label):
        for f in custom_fields or []:
            nome = _norm(f.get("name") or f.get("fieldKey") or "")
            if nome and nome == alvo:
                return f.get("id")
    return None


# Etapas de fechamento — um lead novo nunca entra por elas.
_TERMINAIS = ("ganho", "won", "perdido", "lost", "fechado", "closed", "cliente", "descartado")


def _etapa_terminal(nome: Any) -> bool:
    n = _norm(nome)
    return any(t in n for t in _TERMINAIS)


def pick_pipeline_stage(pipelines: list) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    (pipeline_id, stage_id, motivo_da_recusa). Só escolhe quando é INEQUÍVOCO.

    Com mais de um funil não há como saber qual é o de leads — e adivinhar jogaria
    as oportunidades no lugar errado, em silêncio. Nesse caso devolvemos o motivo
    para o operador escolher.
    """
    validos = [p for p in (pipelines or []) if (p.get("stages") or [])]
    if not validos:
        return None, None, "O CRM não tem nenhum funil com etapas."
    if len(validos) > 1:
        nomes = ", ".join((p.get("name") or "?") for p in validos[:4])
        return None, None, f"O CRM tem {len(validos)} funis ({nomes}…) — escolha qual receberá os leads."

    pipeline = validos[0]
    # A ordem da API não é garantida: ordenar por `position` para pegar a etapa de
    # ENTRADA, e nunca uma terminal — cair em "Ganho"/"Perdido" faria todo lead
    # novo nascer como negócio fechado e sumir do trabalho do time.
    etapas = sorted(
        pipeline.get("stages") or [],
        key=lambda s: s.get("position") if isinstance(s.get("position"), int) else 10**6,
    )
    entrada = [s for s in etapas if not _etapa_terminal(s.get("name"))]
    if not entrada:
        return None, None, (
            f"O funil '{pipeline.get('name') or '?'}' só tem etapas de fechamento — "
            f"escolha a etapa de entrada."
        )
    return pipeline.get("id"), entrada[0].get("id"), None


async def build_agent_provisioning(
    location_id: str, form_data: dict, fields_override: Optional[list[dict]] = None
) -> dict:
    """
    Monta a parte da config do agente que NÃO é o prompt, e explica o que fez.

    Fonte dos campos, em ordem de prioridade:
    1. `fields_override` — o AgentSpec da Mestre (intenção estruturada: label +
       description + type). É a fonte definitiva quando a Mestre roda.
    2. o parser do texto livre do formulário — fallback do que o operador digita.

    Em ambos os casos `key`/`ghl_field_id`/pipeline/stage são resolvidos AQUI, por
    código, contra o CRM real — nunca vêm do LLM. Devolve `config` (só as chaves a
    gravar) e `report` (o que ligou e o que ficou pendente) — auditável, não mágico.
    """
    pendencias: list[str] = []
    if fields_override is not None:
        campos = _com_chaves(fields_override)
    else:
        campos = derive_qualification_fields(form_data.get("qualification_questions"))

    if not campos:
        return {
            "config": {"qualification_enabled": False},
            "report": {
                "qualification_enabled": False,
                "fields": [],
                "pendencias": ["Nenhuma pergunta de qualificação no formulário — o agente só conversa."],
            },
        }

    catalogo = await fetch_crm_catalog(location_id)
    pipeline_id = stage_id = None
    mapeados = 0

    if catalogo.get("ok"):
        for campo in campos:
            fid = match_ghl_field(campo["label"], catalogo["fields"])
            if fid:
                campo["ghl_field_id"] = fid
                mapeados += 1
        pipeline_id, stage_id, motivo = pick_pipeline_stage(catalogo["pipelines"])
        if motivo:
            pendencias.append(motivo)
    else:
        pendencias.append(catalogo.get("error") or "CRM indisponível.")

    nao_mapeados = len(campos) - mapeados
    if nao_mapeados:
        pendencias.append(
            f"{nao_mapeados} de {len(campos)} campos sem correspondente no CRM — "
            f"o dado fica no lead qualificado, mas não sobe para o CRM."
        )

    # FAIL-CLOSED DE VERDADE: sem funil definido a qualificação fica DESLIGADA.
    #
    # Ligar sem pipeline/stage seria a pior falha possível, e é silenciosa: o
    # agente ganharia a tool, diria ao lead que registrou, o handler pularia o
    # CRM (`qualification_handler.py:67` exige pipeline_id e stage_id) sem nem
    # emitir warning, gravaria o QualifiedLead — e a partir daí `ai_service.py:319`
    # PAUSA A IA PARA SEMPRE naquele lead. Pior: o guard de idempotência impede
    # que ele seja reenviado ao CRM mesmo depois de o operador corrigir o funil.
    # Ou seja: o lead conversa, é dado como qualificado, não chega ao CRM e fica
    # sem resposta. Melhor não prometer qualificar do que prometer e engolir.
    pronto = bool(pipeline_id and stage_id)
    if not pronto:
        pendencias.append(
            "Qualificação DESLIGADA até o funil ser definido — o agente conversa "
            "normalmente, mas não registra leads."
        )

    config = {
        "qualification_enabled": pronto,
        "qualification_fields": campos,
        "qualification_pipeline_id": pipeline_id,
        "qualification_stage_id": stage_id,
    }
    return {
        "config": config,
        "report": {
            "qualification_enabled": pronto,
            "fields": [c["label"] for c in campos],
            "campos_mapeados_no_crm": mapeados,
            "pipeline_definido": pronto,
            "pendencias": pendencias,
        },
    }
