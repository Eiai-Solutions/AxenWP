"""
Sessão da entrevista: persistência, escopo por tenant e a saída para o fluxo que já existe.

Divisão de responsabilidade, de propósito:
  `master_interview`  → o loop de tool-use (não sabe o que é banco nem tenant)
  `interview_session` → onde a conversa mora, de quem ela é, e o que vira no fim

Quando a entrevista conclui, ela cria uma `OnboardingSubmission` — exatamente o
que o formulário público produz. O caminho daí para frente (operador revisa,
manda gerar, `generate_agent_spec`, provisionamento) é o que já existia e já é
testado. A entrevista é uma porta nova para um corredor conhecido.
"""

from __future__ import annotations

import asyncio
import secrets
from datetime import datetime, timezone
from typing import Optional

from data.database import SessionLocal
from data.models import AgentInterview, OnboardingSubmission, Tenant
from services.master_interview import (
    EntrevistaIndisponivel,
    EstadoEntrevista,
    avancar,
    historico_visivel,
    ultima_fala,
)
from utils.logger import logger


class SessaoInvalida(RuntimeError):
    """Token desconhecido, encerrado, ou de outro tenant."""


def _agora() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Persistência (síncrona; os chamadores async usam asyncio.to_thread)
# --------------------------------------------------------------------------- #
def _criar_sync(location_id: str) -> str:
    token = secrets.token_urlsafe(24)
    db = SessionLocal()
    try:
        db.add(AgentInterview(
            token=token, tenant_location_id=location_id,
            estado=EstadoEntrevista().to_json(), status="open",
            created_at=_agora(), updated_at=_agora(),
        ))
        db.commit()
        return token
    finally:
        db.close()


def _carregar_sync(token: str) -> tuple[str, EstadoEntrevista, str, Optional[int]]:
    db = SessionLocal()
    try:
        linha = db.query(AgentInterview).filter(AgentInterview.token == token).first()
        if linha is None:
            raise SessaoInvalida("Entrevista não encontrada.")
        return (
            linha.tenant_location_id,
            EstadoEntrevista.from_json(linha.estado),
            linha.status,
            linha.submission_id,
        )
    finally:
        db.close()


def _salvar_sync(token: str, estado: EstadoEntrevista, submission_id: Optional[int]) -> None:
    db = SessionLocal()
    try:
        linha = db.query(AgentInterview).filter(AgentInterview.token == token).first()
        if linha is None:
            return
        linha.estado = estado.to_json()
        linha.tokens_saida = estado.tokens_saida
        linha.updated_at = _agora()
        if estado.concluida:
            linha.status = "concluded"
        if submission_id is not None:
            linha.submission_id = submission_id
        db.commit()
    finally:
        db.close()


def _criar_submission_sync(location_id: str, form_data: dict) -> Optional[int]:
    """
    A entrevista vira exatamente o que o formulário produz.

    Best-effort de propósito: se isto falhar, a entrevista continua gravada e o
    operador não perde as respostas do cliente — mesmo princípio do form público,
    que grava antes de qualquer geração.
    """
    db = SessionLocal()
    try:
        sub = OnboardingSubmission(
            tenant_location_id=location_id, form_data=form_data,
            status="pending", created_at=_agora(),
        )
        db.add(sub)
        db.commit()
        db.refresh(sub)
        logger.info(f"[ENTREVISTA] Submission {sub.id} criada para {location_id}.")
        return sub.id
    except Exception as e:
        db.rollback()
        logger.error(f"[ENTREVISTA] Falha ao criar submission de {location_id}: {e}")
        return None
    finally:
        db.close()


def _tenant_por_form_token_sync(form_token: str) -> Optional[str]:
    db = SessionLocal()
    try:
        t = db.query(Tenant).filter(Tenant.form_token == form_token).first()
        return t.location_id if t else None
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
async def resolver_tenant(form_token: str) -> Optional[str]:
    """location_id do dono do link público, ou None."""
    return await asyncio.to_thread(_tenant_por_form_token_sync, form_token)


async def iniciar(location_id: str) -> dict:
    """Abre a entrevista e devolve a primeira fala da Mestre + o token da sessão."""
    token = await asyncio.to_thread(_criar_sync, location_id)
    resultado = await conversar(token, None)
    # O token volta aqui porque é o que o browser guarda para continuar a conversa.
    return {**resultado, "token": token}


async def conversar(token: str, mensagem: Optional[str],
                    location_id_esperado: Optional[str] = None) -> dict:
    """
    Um turno. `mensagem=None` só na abertura.

    `location_id_esperado` é a barreira de escopo: quem chama pelo link público de
    um tenant não pode continuar a entrevista de outro só por conhecer o token.
    """
    location_id, estado, status, submission_id = await asyncio.to_thread(_carregar_sync, token)

    if location_id_esperado and location_id != location_id_esperado:
        raise SessaoInvalida("Entrevista não pertence a este link.")

    if status == "concluded":
        return {
            "concluida": True,
            "historico": historico_visivel(estado),
            "form_data": estado.form_data,
            "submission_id": submission_id,
        }

    try:
        estado = await avancar(estado, mensagem)
    except EntrevistaIndisponivel:
        # `avancar` muta o estado NO LUGAR, então aqui ele já tem os contadores do
        # que foi gasto antes de estourar — inclusive buscas na web, que a Anthropic
        # já cobrou. Sem este save o banco volta ao valor anterior e cada nova
        # tentativa refaz as MESMAS buscas pagas: o teto de abuso nunca fecha, e o
        # link é público e anônimo. Salvar também preserva o que a pessoa escreveu.
        await asyncio.to_thread(_salvar_sync, token, estado, None)
        raise

    nova_submission = None
    if estado.concluida and estado.form_data:
        nova_submission = await asyncio.to_thread(
            _criar_submission_sync, location_id, estado.form_data
        )

    await asyncio.to_thread(_salvar_sync, token, estado, nova_submission)

    return {
        "concluida": estado.concluida,
        "pergunta": ultima_fala(estado),
        "historico": historico_visivel(estado),
        "form_data": estado.form_data,
        "submission_id": nova_submission or submission_id,
        "turnos": estado.turnos,
    }


async def carregar_para_exibir(token: str, location_id_esperado: Optional[str] = None) -> dict:
    """Estado atual sem gastar token de LLM — para a tela reabrir a conversa."""
    location_id, estado, status, submission_id = await asyncio.to_thread(_carregar_sync, token)
    if location_id_esperado and location_id != location_id_esperado:
        raise SessaoInvalida("Entrevista não pertence a este link.")
    return {
        "concluida": status == "concluded",
        "historico": historico_visivel(estado),
        "form_data": estado.form_data,
        "submission_id": submission_id,
        "turnos": estado.turnos,
    }


__all__ = [
    "EntrevistaIndisponivel",
    "SessaoInvalida",
    "carregar_para_exibir",
    "conversar",
    "iniciar",
    "resolver_tenant",
]
