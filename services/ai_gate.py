"""
A política de "a IA pode responder esta conversa?" — em UM lugar só.

Até 2026-08-20 esta pergunta era respondida em três lugares que discordavam:

  · `inbound_pipeline.ai_is_enabled` (caminho WAHA)
  · uma reimplementação inline dentro de `webhooks/zapi_receiver.py`
  · `ai_service.process_incoming_message`, que consultava `is_already_qualified_sync`

E o Telegram não chamava nenhum dos dois primeiros: a única coisa que calava uma
conversa de Telegram era o terceiro. Resultado prático: pausar um contato pelo
CRM pausava o WhatsApp e **não** pausava o Telegram do mesmo número.

Pior, as duas cópias do gate divergiram sem ninguém notar — nos comentários delas
está escrito que o mesmo bug (não filtrar canal) foi corrigido numa e "ficou para
trás" na outra. Duas cópias da mesma ideia só permanecem iguais por sorte.

A ordem das checagens não é arbitrária: as baratas e locais primeiro, a de rede
por último. O portão do GHL faz DUAS chamadas HTTP num client de `timeout=30s`,
por turno, num pipeline cuja janela de debounce é de 1,5s — ele só deve ser
consultado quando todo o resto já liberou.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from utils.logger import logger

# Motivos de pausa, para o painel poder dizer QUEM calou a IA e por quê.
HANDOFF = "handoff"
QUALIFICADO = "qualificado"
OPERADOR = "operador"
CRM = "crm"
SISTEMA = "sistema"


def _estado_sync(location_id: str, channel: str, contact_ref: str):
    """A linha de estado desta conversa, ou None. Expiração é resolvida aqui."""
    from data.database import SessionLocal
    from data.models import ConversationAIState

    db = SessionLocal()
    try:
        linha = (
            db.query(ConversationAIState)
            .filter(
                ConversationAIState.location_id == location_id,
                ConversationAIState.channel == channel,
                ConversationAIState.contact_ref == contact_ref,
            )
            .first()
        )
        if linha is None:
            return None
        # `until` no passado = a pausa venceu. Não reescrevemos a linha aqui de
        # propósito: o gate roda no caminho quente e não deve escrever. Quem
        # normaliza é `definir`, na próxima mudança de estado.
        vencida = (
            not linha.enabled
            and linha.until is not None
            and linha.until <= datetime.utcnow()
        )
        return {
            "enabled": True if vencida else bool(linha.enabled),
            "motivo": linha.motivo,
            "until": linha.until,
            "mudado_por": linha.mudado_por,
            "vencida": vencida,
        }
    finally:
        db.close()


def _agente_ativo_sync(location_id: str, channel: str) -> bool:
    """
    Agente existe e está ligado NESTE canal — resolvendo alias de canal.

    O filtro de canal é load-bearing: sem ele, `.first()` devolvia um agente
    arbitrário da instância, e desligar o agente do Telegram derrubava a IA do
    WhatsApp sem nenhum erro no log.

    E a resolução de `linked_to_channel` também é. Quando o operador vincula um
    canal a outro (a opção default do painel: "Vincular ao agente WhatsApp"),
    nasce uma LINHA-ESPELHO em `ai_agents` só com nome, prompt e o ponteiro —
    `is_active` cai no default do ORM, que é **False**. Quem lê a linha crua
    conclui "agente desligado" e cala o canal inteiro, para todos os contatos,
    sem prazo e sem nada em `conversation_ai_state` para religar.

    Antes deste gate existir, quem atendia esse caso era
    `ai_service._get_agent_for_tenant_sync`, que resolve o alias ANTES de olhar
    `is_active`. Ao trazer a decisão para cá, a resolução tinha que vir junto.
    """
    from data.database import SessionLocal
    from data.models import AIAgent

    db = SessionLocal()
    try:
        agente = (
            db.query(AIAgent)
            .filter(AIAgent.location_id == location_id, AIAgent.channel == channel)
            .first()
        )
        alvo = getattr(agente, "linked_to_channel", None) if agente else None
        if alvo and alvo != channel:
            real = (
                db.query(AIAgent)
                .filter(AIAgent.location_id == location_id, AIAgent.channel == alvo)
                .first()
            )
            # Alias apontando para canal que sumiu: fica o espelho (inativo) e o
            # canal não responde. Fail-closed de propósito — responder com um
            # agente que ninguém configurou é pior que ficar quieto.
            if real is not None:
                agente = real
        return bool(agente and agente.is_active)
    finally:
        db.close()


async def pode_responder(
    location_id: str,
    channel: str,
    contact_ref: str,
    tenant=None,
    contact_id: Optional[str] = None,
) -> bool:
    """
    A IA pode responder esta conversa agora?

    Chamado por TODOS os caminhos de entrada (WAHA, Z-API, Telegram). Se um
    caminho novo não chamar isto, ele não tem gate — é assim que o Telegram ficou
    sem nenhum por meses.
    """
    import asyncio

    if not await asyncio.to_thread(_agente_ativo_sync, location_id, channel):
        logger.info(f"[GATE] {channel}/{contact_ref}: agente inativo em {location_id}.")
        return False

    estado = await asyncio.to_thread(_estado_sync, location_id, channel, contact_ref)
    if estado is not None:
        if estado["vencida"]:
            logger.info(
                f"[GATE] {channel}/{contact_ref}: pausa por {estado['motivo']} venceu "
                f"em {estado['until']}; IA volta a responder."
            )
        elif not estado["enabled"]:
            logger.info(
                f"[GATE] {channel}/{contact_ref}: IA pausada "
                f"(motivo={estado['motivo']}, por={estado['mudado_por']})."
            )
            return False

    # O portão do CRM fica por ÚLTIMO: é o único que custa rede. Só vale no modo
    # com CRM e quando há contato resolvido — e é fail-closed lá dentro, então
    # consultá-lo à toa transforma instabilidade do CRM em silêncio da IA.
    if getattr(tenant, "mode", "ghl") != "whatsapp_only" and contact_id:
        from services.ghl_service import ghl_service

        return await ghl_service.is_ai_active_for_contact(location_id, contact_id)

    return True


async def pausado_durante_a_espera(
    location_id: str, channel: str, contact_ref: str
) -> bool:
    """
    Re-checagem barata DEPOIS da janela de debounce, imediatamente antes de gerar.

    O gate roda quando a mensagem chega, e a task então **dorme** (1,5s por padrão,
    mais o tempo de gerar e de transcrever áudio). Nesse intervalo o operador pode
    assumir a conversa, o CRM pode mandar pausar, ou o turno anterior pode ter
    escalado — e a task acordaria e responderia por cima de tudo isso.

    Essa propriedade existia por acaso: o portão que morava no `ai_service` rodava
    depois do sono. Ao trazer a decisão para a borda, ela se perdeu; isto a devolve
    de propósito, e sem o custo do portão inteiro — só o estado local, que são duas
    queries baratas. O campo do CRM não é relido aqui: dobrar a chamada HTTP por
    turno para cobrir uma janela de 1,5s não se paga.
    """
    import asyncio

    try:
        estado = await asyncio.to_thread(_estado_sync, location_id, channel, contact_ref)
    except Exception as e:
        # FAIL-OPEN, ao contrário do gate de entrada — e de propósito.
        #
        # O gate já decidiu "pode responder" 1,5s atrás; esta é uma segunda opinião
        # sobre uma janela curtíssima. Se o banco piscar agora, engolir a resposta
        # deixa o lead sem retorno para se proteger de uma corrida que quase nunca
        # acontece. O risco de falar por cima de um humano nesse 1,5s é menor que o
        # de emudecer o atendimento toda vez que o banco tosse.
        logger.warning(
            f"[GATE] Falha ao reler o estado de {channel}/{contact_ref} após o "
            f"debounce ({e}); seguindo com a resposta já autorizada na entrada."
        )
        return False

    if estado is not None and not estado["enabled"]:
        logger.info(
            f"[GATE] {channel}/{contact_ref}: pausada DURANTE a espera do debounce "
            f"(motivo={estado['motivo']}, por={estado['mudado_por']}); resposta abortada."
        )
        return True
    return False


def religar_todos_os_canais(
    location_id: str, contact_ref: str, mudado_por: str = "operador"
) -> int:
    """
    Religa a IA desta conversa em TODOS os canais. Devolve quantos religou.

    Existe porque o caminho do operador é por contato, não por canal: quem clica
    "religar" no painel está falando da pessoa, não do WhatsApp dela. Sem isto, o
    conserto que separou o interruptor do registro deixaria o operador sem botão —
    era `DELETE .../qualification` que religava, e a pausa não mora mais lá.
    """
    from data.database import SessionLocal
    from data.models import ConversationAIState

    db = None
    try:
        db = SessionLocal()
        linhas = (
            db.query(ConversationAIState)
            .filter(
                ConversationAIState.location_id == location_id,
                ConversationAIState.contact_ref == contact_ref,
                ConversationAIState.enabled.is_(False),
            )
            .all()
        )
        for linha in linhas:
            linha.enabled = True
            linha.motivo = None
            linha.until = None          # senão a pausa vencida ressuscita
            linha.mudado_por = mudado_por
        db.commit()
        if linhas:
            logger.info(
                f"[GATE] IA religada em {len(linhas)} canal(is) para {contact_ref} "
                f"@ {location_id} (por {mudado_por})."
            )
        return len(linhas)
    except Exception as e:
        if db is not None:
            db.rollback()
        logger.error(f"[GATE] Falha ao religar {contact_ref}: {e}")
        return 0
    finally:
        if db is not None:
            db.close()


def definir(
    location_id: str,
    channel: str,
    contact_ref: str,
    enabled: bool,
    motivo: Optional[str] = None,
    mudado_por: Optional[str] = None,
    minutos: Optional[int] = None,
) -> bool:
    """
    Liga ou desliga a IA nesta conversa. Sync — chamar via `asyncio.to_thread`.

    `minutos` dá prazo à pausa. É o que impede o handoff de virar mudez eterna:
    quem transfere para um humano está descrevendo um evento em curso, não
    decretando que aquele número nunca mais será atendido.

    Religar (`enabled=True`) **zera o prazo**, senão uma pausa vencida ressuscita
    na próxima vez que alguém olhar a linha.
    """
    from datetime import timedelta

    from data.database import SessionLocal
    from data.models import ConversationAIState

    # A abertura da sessão fica DENTRO do try: se o banco estiver fora, `SessionLocal()`
    # levanta antes de qualquer `except`, e a exceção sobe para o handoff — desfazendo
    # em erro o turno que já foi gerado e já foi pago.
    db = None
    try:
        db = SessionLocal()
        linha = (
            db.query(ConversationAIState)
            .filter(
                ConversationAIState.location_id == location_id,
                ConversationAIState.channel == channel,
                ConversationAIState.contact_ref == contact_ref,
            )
            .first()
        )
        until = (
            datetime.utcnow() + timedelta(minutes=minutos)
            if (minutos and not enabled)
            else None
        )
        if linha is None:
            linha = ConversationAIState(
                location_id=location_id, channel=channel, contact_ref=contact_ref,
            )
            db.add(linha)
        linha.enabled = enabled
        linha.motivo = motivo
        linha.until = until
        linha.mudado_por = mudado_por
        db.commit()
        logger.info(
            f"[GATE] {channel}/{contact_ref} @ {location_id}: IA "
            f"{'LIGADA' if enabled else 'PAUSADA'} (motivo={motivo}, por={mudado_por}"
            + (f", até {until}" if until else "") + ")"
        )
        return True
    except Exception as e:
        # Nunca levanta: o chamador é o handoff ou a qualificação, e o trabalho
        # deles já foi feito e já foi pago. Falhar aqui não pode desfazer aquilo.
        if db is not None:
            db.rollback()
        logger.error(f"[GATE] Falha ao definir estado de {contact_ref}: {e}")
        return False
    finally:
        if db is not None:
            db.close()
