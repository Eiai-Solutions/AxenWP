"""
Handoff para humano — materializa a escalação (que hoje é código morto).

Efeito, escolhido com o dono do produto: PAUSAR a IA nesta conversa (kill-switch)
+ deixar uma NOTA no CRM para o operador assumir com contexto. Diferente da
qualificação, NÃO cria oportunidade — só transfere.

Chamado pelo pipeline (que tem tenant + contact_id), disparado quando o motor
Claude chama a tool `escalate_to_human`. Best-effort: falha aqui não derruba o
turno.
"""

from services.ghl_service import ghl_service
from utils import metrics
from utils.logger import logger


# Prazo da pausa por handoff. Transferir descreve um evento em curso — alguém vai
# assumir agora —, não um decreto de que aquele número nunca mais será atendido.
# Sem prazo, o lead que volta três semanas depois nunca é respondido por ninguém.
MINUTOS_DE_PAUSA_NO_HANDOFF = 24 * 60


async def handle_escalation(
    location_id: str,
    phone: str,
    contact_id: str | None,
    tenant,
    reason: str,
    channel: str = "whatsapp",
) -> None:
    import asyncio

    from services import ai_gate

    is_whatsapp_only = getattr(tenant, "mode", "ghl") == "whatsapp_only"
    logger.warning(f"[HANDOFF] Escalando {phone} @ {location_id} para humano: {reason!r}")
    metrics.inc("millochat_escalations_total", labels={"reason": "tool"})

    # A pausa acontece SEMPRE e AGORA — mas ONDE ela mora depende de quem consegue
    # segurá-la, e isso decide também quem consegue DESFAZÊ-LA.
    #
    # Que ela aconteça não é escolha de arquitetura, é honestidade do produto: a
    # description da tool `escalate_to_human` — que vive no prefixo cacheado e o
    # modelo lê todo turno — promete "PAUSA a IA nesta conversa", o dispatch
    # confirma "conversa transferida", e o agente já disse isso ao lead em voz alta.
    #
    # No modo com CRM, o interruptor que o operador VÊ e MEXE é o campo "Status IA"
    # do contato. Se conseguirmos escrever nele, ele é a pausa — e voltar para
    # "Ativada" religa, como sempre religou. Criar ALÉM dele uma pausa local de 24h
    # tornaria esse religar inócuo: o operador marcaria Ativada e a IA seguiria
    # muda, sem nada no log explicando por quê.
    #
    # A pausa local é o fallback para quando o CRM não pode segurar: modo sem CRM,
    # contato não resolvido, campo inexistente, ou a escrita falhando. Aí ela é a
    # única coisa que faz a promessa da tool ser verdade.
    pausou_no_crm = False
    if not is_whatsapp_only and contact_id:
        try:
            field_id = await ghl_service._get_custom_field_id_by_name(location_id, "Status IA")
            if field_id:
                await ghl_service.update_contact(
                    location_id, contact_id,
                    {"customFields": [{"id": field_id, "field_value": "Desativada"}]},
                )
                pausou_no_crm = True
                logger.info(f"[HANDOFF] IA pausada no CRM para contato {contact_id}")
        except Exception as e:
            logger.error(f"[HANDOFF] Falha ao pausar IA de {contact_id} no CRM: {e}")

    if not pausou_no_crm:
        # Antes isto só existia no modo sem CRM, e por um caminho torto: gravava um
        # QualifiedLead FALSO (`_handoff: True`) — quem só pediu atendente entrava
        # na tabela de qualificados e contava na métrica de leads.
        await asyncio.to_thread(
            ai_gate.definir,
            location_id=location_id, channel=channel, contact_ref=phone,
            enabled=False, motivo=ai_gate.HANDOFF, mudado_por="agente",
            minutos=MINUTOS_DE_PAUSA_NO_HANDOFF,
        )

    if is_whatsapp_only or not contact_id:
        return

    # Nota no CRM para o humano assumir.
    try:
        nota = f"🤝 IA transferiu para atendimento humano.\nMotivo: {reason or '(não informado)'}"
        await ghl_service.create_contact_note(location_id, contact_id, nota)
    except Exception as e:
        logger.error(f"[HANDOFF] Falha ao criar nota de handoff em {contact_id}: {e}")
