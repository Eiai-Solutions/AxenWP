"""
Ciclo de vida do rascunho: abrir, salvar etapa, publicar.

Separação de responsabilidade, igual à da entrevista:
  `agent_wizard`   → QUAIS etapas existem para este tenant (função pura)
  `draft_service`  → onde o rascunho mora e o que acontece ao publicar

Publicar é a única operação que cria `AIAgent`. Antes disso o agente não existe
para o runtime — é o que impede um agente pela metade de começar a atender.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional

from data.database import SessionLocal
from data.models import (
    AgentDraft, AgentPromptHistory, AIAgent, OnboardingSubmission, Tenant,
)
from services.agent_wizard import como_json, etapas_para, pode_publicar
from utils.logger import logger


class RascunhoInvalido(RuntimeError):
    """Rascunho inexistente, de outro tenant, ou já publicado."""


def _agora() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Síncrono (os chamadores async usam asyncio.to_thread)
# --------------------------------------------------------------------------- #
def _abrir_sync(location_id: str, operador: Optional[str]) -> int:
    """
    Retoma o rascunho DO PRÓPRIO operador, ou abre um novo.

    O painel é da equipe. Sem o filtro por dono, o "+" de um operador devolvia o
    rascunho meio preenchido do outro: ele achava que começava do zero, salvava por
    cima, e o trabalho do colega sumia sem um aviso — inclusive a config de
    qualificação, que ele herdava sem ter passado pela etapa.
    """
    db = SessionLocal()
    try:
        existente = (
            db.query(AgentDraft)
            .filter(
                AgentDraft.location_id == location_id,
                AgentDraft.status == "rascunho",
                AgentDraft.criado_por == operador,
            )
            .order_by(AgentDraft.id.desc())
            .first()
        )
        if existente:
            return existente.id

        tenant = db.query(Tenant).filter(Tenant.location_id == location_id).first()
        etapas = etapas_para(tenant) if tenant else []
        # Um canal só: já deixamos escolhido, para a etapa não pedir o óbvio.
        canal = None
        for e in etapas:
            if e.id == "canal" and e.variante == "unico":
                canal = e.dados.get("escolhido")

        d = AgentDraft(
            location_id=location_id, channel=canal, etapa_atual="canal",
            criado_por=operador, status="rascunho",
            created_at=_agora(), updated_at=_agora(),
        )
        db.add(d)
        db.commit()
        db.refresh(d)
        logger.info(f"[WIZARD] Rascunho {d.id} aberto para {location_id}.")
        return d.id
    finally:
        db.close()


_CAMPOS_EDITAVEIS = {
    "channel", "etapa_atual", "origem", "interview_token", "submission_id",
    "agent_name", "prompt", "spec", "qualificar", "qualification_fields",
    "qualification_pipeline_id", "qualification_stage_id",
}


_TEXTO_CURTO = {"channel": 20, "etapa_atual": 32, "origem": 20,
                "interview_token": 64, "agent_name": 120}


def _valor_valido(chave: str, valor: Any) -> Any:
    """
    Confere forma e tamanho antes de persistir.

    O `setattr` cego aceitava qualquer coisa, e o publish copiava para o agente.
    `qualification_fields` como lista de STRINGS (forma plausível para uma tela)
    quebrava `prompt_builder` no caminho quente de TODA mensagem daquele tenant —
    e não há retry em lugar nenhum do projeto, então a mensagem do lead se perdia.
    """
    if valor is None:
        return None

    if chave in _TEXTO_CURTO:
        if not isinstance(valor, str):
            raise RascunhoInvalido(f"Campo '{chave}' precisa ser texto.")
        return valor.strip()[: _TEXTO_CURTO[chave]] or None

    if chave == "prompt":
        if not isinstance(valor, str):
            raise RascunhoInvalido("O prompt precisa ser texto.")
        return valor

    if chave == "qualificar":
        return bool(valor)

    if chave == "submission_id":
        try:
            return int(valor)
        except (TypeError, ValueError):
            raise RascunhoInvalido("submission_id inválido.")

    if chave == "qualification_fields":
        if not isinstance(valor, list):
            raise RascunhoInvalido("Os campos de qualificação precisam ser uma lista.")
        limpos = []
        for item in valor[:12]:  # mesmo teto do AgentSpec: além disso vira interrogatório
            if not isinstance(item, dict) or not str(item.get("label") or "").strip():
                raise RascunhoInvalido("Cada campo precisa ser um objeto com 'label'.")
            limpos.append({
                "label": str(item["label"]).strip()[:120],
                "description": str(item.get("description") or "").strip()[:400],
                "type": str(item.get("type") or "text").strip()[:20],
            })
        return limpos

    if chave == "spec":
        if not isinstance(valor, dict):
            raise RascunhoInvalido("spec precisa ser um objeto.")
        return valor

    return valor


def _salvar_sync(draft_id: int, location_id: str, mudancas: dict) -> dict:
    db = SessionLocal()
    try:
        d = db.query(AgentDraft).filter(AgentDraft.id == draft_id).first()
        if d is None or d.location_id != location_id:
            # Mesma barreira das outras rotas: id sequencial não abre porta de
            # outro tenant.
            raise RascunhoInvalido("Rascunho não encontrado.")
        if d.status != "rascunho":
            raise RascunhoInvalido("Este rascunho já foi publicado.")

        for chave, valor in (mudancas or {}).items():
            if chave not in _CAMPOS_EDITAVEIS:
                continue
            valor = _valor_valido(chave, valor)
            setattr(d, chave, valor)
        d.updated_at = _agora()
        db.commit()
        return _como_dict(d)
    finally:
        db.close()


def _carregar_sync(draft_id: int, location_id: str) -> tuple[dict, Any, Optional[dict]]:
    db = SessionLocal()
    try:
        d = db.query(AgentDraft).filter(AgentDraft.id == draft_id).first()
        if d is None or d.location_id != location_id:
            raise RascunhoInvalido("Rascunho não encontrado.")
        tenant = db.query(Tenant).filter(Tenant.location_id == location_id).first()

        # Já existe agente NESTE canal? Publicar vai SUBSTITUIR o prompt dele, e o
        # operador precisa saber disso ANTES de clicar — a Joorney tem 22k caracteres
        # de prompt em produção. O histórico guarda a versão anterior, mas descobrir
        # depois é a pior hora.
        existente = None
        if d.channel:
            a = (
                db.query(AIAgent)
                .filter(AIAgent.location_id == location_id, AIAgent.channel == d.channel)
                .first()
            )
            if a:
                existente = {
                    "nome": a.name or "Agente IA",
                    "tamanho_prompt": len(a.prompt or ""),
                    "ativo": bool(a.is_active),
                    # O que o aviso escondia e o operador precisa saber:
                    "qualificacao_ligada": bool(a.qualification_enabled),
                    "tem_funil": bool(a.qualification_pipeline_id),
                    "e_alias_de": getattr(a, "linked_to_channel", None),
                }
        return _como_dict(d), tenant, existente
    finally:
        db.close()


def _como_dict(d: AgentDraft) -> dict:
    return {
        "id": d.id, "location_id": d.location_id, "etapa_atual": d.etapa_atual,
        "channel": d.channel, "origem": d.origem, "interview_token": d.interview_token,
        "submission_id": d.submission_id, "agent_name": d.agent_name,
        "prompt": d.prompt, "spec": d.spec, "qualificar": bool(d.qualificar),
        "qualification_fields": d.qualification_fields,
        "qualification_pipeline_id": d.qualification_pipeline_id,
        "qualification_stage_id": d.qualification_stage_id,
        "status": d.status, "agent_id": d.agent_id,
    }


def _publicar_sync(draft_id: int, location_id: str, config_qualif: dict) -> dict:
    """
    Cria (ou atualiza) o `AIAgent`. É aqui que o agente passa a existir.

    `config_qualif` vem de `agent_provisioning.build_agent_provisioning` — NUNCA do
    JSON do cliente. Copiar o rascunho direto contornava o portão fail-closed e
    ligava qualificação sem funil em tenant com CRM: o agente diria ao lead que
    registrou, `qualification_handler` pularia o CRM, o QualifiedLead seria gravado
    e a IA ficaria pausada naquele lead PARA SEMPRE.
    """
    db = SessionLocal()
    try:
        d = db.query(AgentDraft).filter(AgentDraft.id == draft_id).first()
        if d is None or d.location_id != location_id:
            raise RascunhoInvalido("Rascunho não encontrado.")
        if d.status != "rascunho":
            raise RascunhoInvalido("Este rascunho já foi publicado.")

        canal = d.channel
        if not canal:
            # Sem canal explícito é ERRO, não default. O antigo `or "whatsapp"`
            # mirava justamente o slot do agente que atende hoje.
            raise RascunhoInvalido("Rascunho sem canal definido.")
        agente = (
            db.query(AIAgent)
            .filter(AIAgent.location_id == location_id, AIAgent.channel == canal)
            .first()
        )
        criou = agente is None
        prompt_anterior = None if criou else (agente.prompt or "")
        if criou:
            agente = AIAgent(location_id=location_id, channel=canal)
            db.add(agente)

        agente.name = d.agent_name or agente.name or "Agente IA"
        agente.prompt = d.prompt
        agente.agent_engine = "claude"
        # `form_data` alimenta o guardrail de outbound e o improve-prompt. Sem ele
        # o agente nasce de segunda classe: `contains_forbidden_phrase(outbound)`
        # nunca dispara, porque a única fonte do modo é form_data["agent_type"].
        if d.spec:
            agente.form_data = d.spec

        # Qualificação SEMPRE derivada, nunca copiada — e NUNCA destrutiva.
        #
        # O wizard publica o PROMPT. A qualificação de um agente que já atende pode
        # ter vindo de outro lugar (formulário, tela de Configurar Agente, curadoria
        # do operador). Escrever as quatro colunas incondicionalmente APAGAVA isso:
        # `qualification_enabled` virava False, pipeline e stage viravam None, e o
        # agente seguia conversando normalmente — só parava de registrar lead, sem
        # um único erro no log. `admin/ai_agent.py` já preservava de propósito no
        # caminho da submissão; aqui não preservava.
        #
        # Regra: só escreve quando o agente está NASCENDO (não há o que perder) ou
        # quando a derivação de fato ligou a qualificação (intenção realizada).
        # Caso contrário, preserva o que está lá e devolve isso como pendência —
        # em vez de decidir em silêncio pelo operador.
        qualificacao_preservada = False
        if criou or config_qualif.get("qualification_enabled"):
            agente.qualification_enabled = bool(config_qualif.get("qualification_enabled"))
            agente.qualification_fields = config_qualif.get("qualification_fields") or []
            agente.qualification_pipeline_id = config_qualif.get("qualification_pipeline_id")
            agente.qualification_stage_id = config_qualif.get("qualification_stage_id")
        elif agente.qualification_enabled or agente.qualification_fields:
            qualificacao_preservada = True
            logger.info(
                f"[WIZARD] {location_id}/{canal}: qualificação existente PRESERVADA "
                f"(o rascunho não pediu qualificação)."
            )

        # Publicar por cima NÃO religa agente que a equipe pausou de propósito
        # (durante um incidente, por exemplo). Só nasce ligado quem é novo.
        if criou:
            agente.is_active = True

        # Publicar um agente PRÓPRIO neste canal é, por definição, desfazer o alias.
        # Sem isto o registro é gravado, a rota diz sucesso, e `ai_service` continua
        # servindo o agente do canal apontado — o operador testa e recebe a persona
        # errada, sem um único erro no log.
        if getattr(agente, "linked_to_channel", None):
            logger.info(f"[WIZARD] {location_id}/{canal} deixa de ser alias de {agente.linked_to_channel}.")
            agente.linked_to_channel = None

        db.flush()

        # CLAUDE.md → "Snapshot de prompt sempre que `agent.prompt` é escrito".
        # Aqui a regra vale dobrado: publicar SOBRESCREVE o prompt do agente que já
        # atende (a Joorney tem 22k caracteres). Sem o histórico, um wizard concluído
        # por engano apagaria isso sem volta.
        #
        # Gravado na MESMA transação, e não via `prompt_history.snapshot_prompt`,
        # que abre sessão própria: sessão aninhada dentro desta trava o SQLite
        # ("database is locked") e, no Postgres, deixaria o snapshot em transação
        # separada — órfão se o publish desse rollback. Aqui ou os dois landam ou
        # nenhum.
        if prompt_anterior and prompt_anterior != (d.prompt or ""):
            db.add(AgentPromptHistory(
                location_id=location_id, channel=canal, agent_id=agente.id,
                source="wizard_overwrite", prompt=prompt_anterior,
                note="Versão substituída ao publicar o assistente de criação.",
            ))
        db.add(AgentPromptHistory(
            location_id=location_id, channel=canal, agent_id=agente.id,
            source="wizard_publish", prompt=d.prompt or "",
        ))

        # A submissão que originou este rascunho vira 'processed' só AGORA. Marcar
        # na importação faria o operador que importa e desiste consumir o trabalho
        # que o cliente teve de responder — a submissão sumiria da aba sem nunca
        # ter virado agente. Na mesma transação do publish: ou os dois landam, ou
        # nenhum.
        if d.submission_id:
            sub = (
                db.query(OnboardingSubmission)
                .filter(
                    OnboardingSubmission.id == d.submission_id,
                    OnboardingSubmission.tenant_location_id == location_id,
                )
                .first()
            )
            if sub and sub.status == "pending":
                sub.status = "processed"
                sub.processed_at = _agora()

        d.status = "publicado"
        d.agent_id = agente.id
        d.updated_at = _agora()
        db.commit()
        logger.info(
            f"[WIZARD] Rascunho {draft_id} publicado: agente {agente.id} "
            f"({'criado' if criou else 'atualizado'}) em {location_id}/{canal}."
        )
        return {
            "agent_id": agente.id, "criado": criou, "channel": canal,
            "qualificacao_preservada": qualificacao_preservada,
        }
    except RascunhoInvalido:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"[WIZARD] Falha ao publicar rascunho {draft_id}: {type(e).__name__}")
        raise
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
async def abrir(location_id: str, operador: Optional[str] = None) -> dict:
    draft_id = await asyncio.to_thread(_abrir_sync, location_id, operador)
    return await estado(draft_id, location_id)


def _tem_chave_do_motor() -> bool:
    """
    O agente nasce no motor `claude`, que precisa de chave Anthropic (própria ou a
    do admin). Sem nenhuma, `ai_service` cai em `engine = None` e o agente
    simplesmente NÃO RESPONDE — o operador acha que criou e o lead fica no vácuo.
    Checar aqui transforma isso num impedimento explícito antes do clique.
    """
    try:
        from services.master_engine import _resolve_master_key

        return bool(_resolve_master_key())
    except Exception as e:
        logger.warning(f"[WIZARD] Não consegui verificar a chave do motor: {type(e).__name__}")
        return True  # na dúvida não bloqueia; o log fica


async def estado(draft_id: int, location_id: str) -> dict:
    rascunho, tenant, existente = await asyncio.to_thread(_carregar_sync, draft_id, location_id)
    etapas = etapas_para(tenant) if tenant else []
    ok, motivo = pode_publicar(rascunho, etapas)

    if ok and not await asyncio.to_thread(_tem_chave_do_motor):
        ok, motivo = False, (
            "Nenhuma chave Anthropic configurada — o agente seria publicado e não "
            "responderia. Configure em Config. Admin → IA Mestre."
        )
    return {
        "rascunho": rascunho,
        "etapas": como_json(etapas),
        "pode_publicar": ok,
        "impedimento": motivo,
        # Não bloqueia — avisa. Substituir é uma operação legítima; fazer sem saber
        # é que não é.
        "vai_substituir": existente,
    }


def _submissao_pendente_sync(location_id: str) -> Optional[dict]:
    """
    A resposta mais recente do cliente que ainda não virou agente.

    Sem filtro de data de propósito: o cliente pode ter preenchido ontem, e exigir
    que fosse depois de o rascunho abrir esconderia justamente o que o operador
    está procurando.
    """
    db = SessionLocal()
    try:
        s = (
            db.query(OnboardingSubmission)
            .filter(
                OnboardingSubmission.tenant_location_id == location_id,
                OnboardingSubmission.status == "pending",
            )
            .order_by(OnboardingSubmission.id.desc())
            .first()
        )
        return {"id": s.id, "form_data": dict(s.form_data or {})} if s else None
    finally:
        db.close()


async def submissao_pendente(location_id: str) -> Optional[dict]:
    """Acesso a banco mora no service — a rota só orquestra (e roda a Mestre)."""
    return await asyncio.to_thread(_submissao_pendente_sync, location_id)


async def salvar(draft_id: int, location_id: str, mudancas: dict) -> dict:
    await asyncio.to_thread(_salvar_sync, draft_id, location_id, mudancas)
    return await estado(draft_id, location_id)


async def publicar(draft_id: int, location_id: str) -> dict:
    """
    Publica, mas só se `pode_publicar` autorizar.

    O guard é reavaliado AQUI, não confiado do que a tela mandou: a tela pode
    estar desatualizada, e publicar agente sem prompt o põe atendendo lead com
    nada a dizer.
    """
    atual = await estado(draft_id, location_id)
    if not atual["pode_publicar"]:
        raise RascunhoInvalido(atual["impedimento"] or "Rascunho incompleto.")

    rascunho, tenant, _ = await asyncio.to_thread(_carregar_sync, draft_id, location_id)

    # A config de qualificação é DERIVADA aqui, contra o CRM real — o mesmo portão
    # que `agent_provisioning` aplica ao formulário. O rascunho só diz a INTENÇÃO
    # ("quero qualificar") e quais campos; quem decide se dá para ligar é o código.
    config_qualif: dict = {"qualification_enabled": False, "qualification_fields": []}
    pendencias: list = []
    if rascunho.get("qualificar"):
        from services.agent_provisioning import build_agent_provisioning

        prov = await build_agent_provisioning(
            location_id,
            rascunho.get("spec") or {},
            fields_override=rascunho.get("qualification_fields") or None,
            tenant=tenant,
        )
        config_qualif = prov["config"]
        pendencias = prov["report"].get("pendencias") or []

    resultado = await asyncio.to_thread(
        _publicar_sync, draft_id, location_id, config_qualif
    )
    return {**resultado, "pendencias": pendencias, **await estado(draft_id, location_id)}


__all__ = ["RascunhoInvalido", "abrir", "estado", "publicar", "salvar"]
