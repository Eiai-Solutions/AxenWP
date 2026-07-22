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


def _pausar_whatsapp_only(location_id: str, phone: str, reason: str) -> None:
    """Kill-switch durável no modo sem CRM: linha em QualifiedLead (o gate da IA)."""
    from data.database import SessionLocal
    from data.models import QualifiedLead

    db = SessionLocal()
    try:
        existe = db.query(QualifiedLead).filter(
            QualifiedLead.location_id == location_id, QualifiedLead.phone == phone
        ).first()
        if existe:
            return  # já pausado/qualificado — idempotente
        db.add(QualifiedLead(
            location_id=location_id, phone=phone,
            qualified_data={"_handoff": True, "reason": reason},
            summary=f"Transferido para humano: {reason}",
        ))
        db.commit()
        logger.info(f"[HANDOFF] IA pausada (whatsapp_only) para {phone} @ {location_id}")
    except Exception as e:
        db.rollback()
        logger.error(f"[HANDOFF] Falha ao pausar (whatsapp_only) {phone}: {e}")
    finally:
        db.close()


async def handle_escalation(
    location_id: str,
    phone: str,
    contact_id: str | None,
    tenant,
    reason: str,
    channel: str = "whatsapp",
) -> None:
    is_whatsapp_only = getattr(tenant, "mode", "ghl") == "whatsapp_only"
    logger.warning(f"[HANDOFF] Escalando {phone} @ {location_id} para humano: {reason!r}")
    metrics.inc("axenwp_escalations_total", labels={"reason": "tool"})

    if is_whatsapp_only or not contact_id:
        # Sem CRM não há custom field para pausar. O kill-switch durável nesse modo
        # é o mesmo que a qualificação usa: uma linha em QualifiedLead — o gate da
        # IA (ai_is_enabled/is_already_qualified) pausa a conversa quando ela existe.
        # Marcamos como handoff em qualified_data para distinguir de qualificação real.
        _pausar_whatsapp_only(location_id, phone, reason)
        return

    # Kill-switch: mesma pausa que a qualificação usa (custom field "Status IA").
    try:
        field_id = await ghl_service._get_custom_field_id_by_name(location_id, "Status IA")
        if field_id:
            await ghl_service.update_contact(
                location_id, contact_id,
                {"customFields": [{"id": field_id, "field_value": "Desativada"}]},
            )
            logger.info(f"[HANDOFF] IA pausada para contato {contact_id}")
    except Exception as e:
        logger.error(f"[HANDOFF] Falha ao pausar IA de {contact_id}: {e}")

    # Nota no CRM para o humano assumir.
    try:
        nota = f"🤝 IA transferiu para atendimento humano.\nMotivo: {reason or '(não informado)'}"
        await ghl_service.create_contact_note(location_id, contact_id, nota)
    except Exception as e:
        logger.error(f"[HANDOFF] Falha ao criar nota de handoff em {contact_id}: {e}")
