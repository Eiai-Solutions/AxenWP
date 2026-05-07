"""
Handler de qualificação: cria oportunidade no GHL e registra QualifiedLead
após o agente extrair os dados completos do lead.

Channel-agnostic — invocado tanto pelo Z-API quanto pelo Telegram receiver.
"""

from typing import Optional

from auth.token_manager import token_manager
from data.database import SessionLocal
from data.models import AIAgent, QualifiedLead
from services.ghl_service import ghl_service
from utils.logger import logger
from utils import metrics


async def handle_qualification(
    location_id: str,
    phone: str,
    contact_id: Optional[str],
    tenant,
    qualified_data: dict,
    summary: str,
    channel: str = "whatsapp",
) -> None:
    """
    Persiste lead qualificado e (se houver token GHL + pipeline configurado)
    cria a oportunidade correspondente. Idempotente: ignora se já qualificado.
    """
    is_whatsapp_only = getattr(tenant, "mode", "ghl") == "whatsapp_only"

    db = SessionLocal()
    try:
        agent = (
            db.query(AIAgent)
            .filter(AIAgent.location_id == location_id, AIAgent.channel == channel)
            .first()
        )
        if not agent:
            logger.error(f"Qualificação: agente não encontrado para {location_id}/{channel}")
            return

        pipeline_id = agent.qualification_pipeline_id
        stage_id = agent.qualification_stage_id
        qualification_fields = agent.qualification_fields or []

        # Idempotência: já qualificado, pula.
        existing = (
            db.query(QualifiedLead)
            .filter(QualifiedLead.location_id == location_id, QualifiedLead.phone == phone)
            .first()
        )
        if existing:
            logger.info(f"Lead {phone} já qualificado anteriormente. Ignorando duplicação.")
            return
    finally:
        db.close()

    opp_id = None
    has_ghl_token = await token_manager.get_valid_token(location_id) is not None
    logger.info(
        f"Qualificação [{phone}]: token={has_ghl_token} pipeline={pipeline_id} "
        f"stage={stage_id} whatsapp_only={is_whatsapp_only} contact={contact_id}"
    )

    if has_ghl_token and pipeline_id and stage_id:
        ghl_contact_id = contact_id

        if not ghl_contact_id:
            existing_contact = await ghl_service.search_contact_by_phone(location_id, phone)
            if existing_contact and "id" in existing_contact:
                ghl_contact_id = existing_contact["id"]
            else:
                _pre_first = None
                for fd in qualification_fields:
                    if fd.get("ghl_field_id") == "contact.firstName" and fd.get("key") in qualified_data:
                        _pre_first = qualified_data[fd["key"]]
                        break
                lead_name_pre = (
                    _pre_first
                    or qualified_data.get("nome")
                    or qualified_data.get("name")
                    or qualified_data.get("nome_completo")
                    or phone
                )
                new_contact = await ghl_service.create_contact(
                    location_id, phone, name=lead_name_pre
                )
                if new_contact and "id" in new_contact:
                    ghl_contact_id = new_contact["id"]
                    token_manager.save_contact_mapping(location_id, phone, ghl_contact_id)

        if ghl_contact_id:
            contact_std_updates: dict = {}
            opp_std_updates: dict = {}
            custom_fields: list = []

            for field_def in qualification_fields:
                ghl_field_id = field_def.get("ghl_field_id") or ""
                key = field_def.get("key")
                if not key or key not in qualified_data or not ghl_field_id:
                    continue
                value = qualified_data[key]

                if ghl_field_id.startswith("contact."):
                    contact_std_updates[ghl_field_id[len("contact."):]] = value
                elif ghl_field_id.startswith("opportunity."):
                    opp_std_updates[ghl_field_id[len("opportunity."):]] = value
                else:
                    custom_fields.append({"id": ghl_field_id, "field_value": value})

            if contact_std_updates:
                await ghl_service.update_contact(
                    location_id, ghl_contact_id, contact_std_updates
                )
                logger.info(
                    f"Contato {ghl_contact_id} atualizado: {list(contact_std_updates.keys())}"
                )

            first = contact_std_updates.get("firstName", "")
            last = contact_std_updates.get("lastName", "")
            lead_name = (
                f"{first} {last}".strip() if first else None
            ) or (
                qualified_data.get("nome")
                or qualified_data.get("name")
                or qualified_data.get("nome_completo")
                or qualified_data.get("full_name")
                or phone
            )
            opp_name = opp_std_updates.get("name") or f"{lead_name} - {channel.capitalize()} Lead"
            monetary_value = 0.0
            if "monetaryValue" in opp_std_updates:
                try:
                    monetary_value = float(opp_std_updates["monetaryValue"])
                except (ValueError, TypeError):
                    pass

            result = await ghl_service.create_opportunity(
                location_id=location_id,
                pipeline_id=pipeline_id,
                stage_id=stage_id,
                contact_id=ghl_contact_id,
                name=opp_name,
                custom_fields=custom_fields if custom_fields else None,
                notes=summary,
                monetary_value=monetary_value,
            )

            if result and not result.get("error"):
                opp_id = result.get("id")
                logger.info(f"Oportunidade criada para lead {phone}: {opp_id}")
            else:
                logger.error(f"Falha ao criar oportunidade para {phone}: {result}")

            if not is_whatsapp_only:
                field_id = await ghl_service._get_custom_field_id_by_name(
                    location_id, "Status IA"
                )
                if field_id:
                    await ghl_service.update_contact(
                        location_id,
                        ghl_contact_id,
                        {"customFields": [{"id": field_id, "field_value": "Desativada"}]},
                    )
                    logger.info(
                        f"Status IA desativado para contato {ghl_contact_id} após qualificação"
                    )
        else:
            logger.error(
                f"Qualificação: não foi possível obter/criar contato GHL para {phone}"
            )
    elif pipeline_id and stage_id and not has_ghl_token:
        logger.warning(
            f"Qualificação configurada mas sem token GHL para {location_id}."
        )

    # Persiste o lead qualificado mesmo sem GHL (serve de flag pro motor de IA)
    db = SessionLocal()
    try:
        ql = QualifiedLead(
            location_id=location_id,
            phone=phone,
            ghl_opportunity_id=opp_id,
            qualified_data=qualified_data,
            summary=summary,
        )
        db.add(ql)
        db.commit()
        logger.info(f"Lead qualificado salvo: {phone} @ {location_id}")
        metrics.inc(
            "axenwp_leads_qualified_total",
            labels={"channel": channel, "ghl_opportunity": "yes" if opp_id else "no"},
        )
    except Exception as e:
        logger.error(f"Erro ao salvar lead qualificado: {e}")
        db.rollback()
    finally:
        db.close()
