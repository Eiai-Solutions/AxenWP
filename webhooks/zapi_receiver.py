"""
Recebe webhooks do Z-API (Inbound).
Quando o cliente responde no WhatsApp, o Z-API avisa este servidor,
que formata e insere no GHL via /conversations/messages/inbound
"""

from fastapi import APIRouter, Request, BackgroundTasks, Path
from typing import Dict, Any

from utils.logger import logger
from utils.config import settings
from auth.token_manager import token_manager
from services.ghl_service import ghl_service


router = APIRouter(prefix="/webhook/zapi", tags=["Webhooks Z-API"])


async def process_inbound_message(location_id: str, payload: Dict[str, Any]):
    """
    Processa a mensagem recebida pelo webhook da Z-API.
    1. Verifica o token do provider para esse location_id.
    2. Extrai texto e anexos.
    3. Manda para a API Inbound do GHL.
    """
    tenant = token_manager.get_tenant(location_id)
    if not tenant:
        logger.error(f"Z-API Inbound abortado: Tenant {location_id} não registrado.")
        return

    # Apenas logamos as infos para facilitar debug
    phone = payload.get("phone", "")
    message_type = payload.get("type", "")
    is_group = payload.get("isGroup", False)
    from_me = payload.get("fromMe", False)

    # Filtrar mensagens indesejadas
    if is_group:
        logger.debug(f"Ignorando mensagem de grupo.")
        return
    if from_me:
        logger.debug(f"Ignorando mensagem enviada por nós mesmos.")
        return
    if message_type not in ["ReceivedCallback", "MessageReceived"]:
        # Pode ajustar dependo de onde no painel Z-API configurou
        pass 

    logger.info(f"Processando inbound Z-API para tenant {location_id} (origem: {phone})")

    content_message = "Mensagem recebida do WhatsApp"
    attachments = []

    # Parse do tipo de mensagem (Z-API possui várias estruturas)
    if "text" in payload and isinstance(payload["text"], dict):
        content_message = payload["text"].get("message", "")
    elif "image" in payload and isinstance(payload["image"], dict):
        content_message = payload["image"].get("caption", "📸 Imagem recebida")
        if "imageUrl" in payload["image"]:
            attachments.append(payload["image"]["imageUrl"])
    elif "audio" in payload and isinstance(payload["audio"], dict):
        content_message = "🎙️ Áudio recebido"
        if "audioUrl" in payload["audio"]:
            attachments.append(payload["audio"]["audioUrl"])
    elif "document" in payload and isinstance(payload["document"], dict):
        content_message = payload["document"].get("fileName", "📄 Documento recebido")
        if "documentUrl" in payload["document"]:
            attachments.append(payload["document"]["documentUrl"])
    
    # Se não conseguimos extrair texto decente mas a Z-API enviou string direto
    if not content_message and isinstance(payload.get("text"), str):
        content_message = payload["text"]

    # 1. Tentar achar o mapeamento no banco de dados local primeiro (útil para @lid e velocidade)
    contact_id = token_manager.get_mapped_contact_id(location_id, phone)
    
    if not contact_id:
        # Se é um @lid e não está no banco, nem adianta pesquisar na API Oficial porque a API do GHL não busca lid.
        # Mas se for telefone normal, tentamos achar lá pra ver se já existe.
        if "@lid" not in phone:
            contact = await ghl_service.search_contact_by_phone(location_id, phone)
            if contact and "id" in contact:
                contact_id = contact["id"]
        
        # Se ainda não temos um contact_id, criamos um novo
        if not contact_id:
            logger.info(f"Contato {phone} não encontrado. Criando novo no GHL...")
            sender_name = payload.get("senderName") or payload.get("participantName") or ""
            # Se vier só o lid, criamos o nome como "Lead do WhatsApp" para não ficar feio no CRM
            if not sender_name and "@lid" in phone:
                sender_name = "Lead do WhatsApp (Anúncio)"
                
            new_contact = await ghl_service.create_contact(location_id, phone, name=sender_name)
            if new_contact and "id" in new_contact:
                contact_id = new_contact["id"]
        
        # 2. Se agora temos um contact_id (achado ou recém-criado), SALVAMOS no banco local
        if contact_id:
            token_manager.save_contact_mapping(location_id, phone, contact_id)

    if not contact_id:
        logger.error(f"Impossível registrar inbound: Falha ao obter/criar contactId para o telefone {phone}")
        return

    # Registrar no CRM
    resp = await ghl_service.send_inbound_message(
        location_id=location_id,
        phone=phone,
        message=content_message,
        attachments=attachments,
        conversation_provider_id=tenant.conversation_provider_id,
        contact_id=contact_id,
    )

    # Detecção de contato deletado no GHL manualmente pelo usuário
    if resp and isinstance(resp, dict) and resp.get("error"):
        if resp.get("status_code") == 400 and "Contact not found/deleted" in str(resp.get("body", {})):
            logger.warning(f"Contato {contact_id} deletado no GHL. Limpando cache e recriando...")
            token_manager.delete_contact_mapping(location_id, phone)
            
            # Recria o contato do zero
            sender_name = payload.get("senderName") or payload.get("participantName") or ""
            if not sender_name and "@lid" in phone:
                sender_name = "Lead do WhatsApp (Anúncio)"
                
            new_contact = await ghl_service.create_contact(location_id, phone, name=sender_name)
            if new_contact and "id" in new_contact:
                contact_id = new_contact["id"]
                token_manager.save_contact_mapping(location_id, phone, contact_id)
                
                # Tenta enviar de novo
                resp = await ghl_service.send_inbound_message(
                    location_id=location_id,
                    phone=phone,
                    message=content_message,
                    attachments=attachments,
                    conversation_provider_id=tenant.conversation_provider_id,
                    contact_id=contact_id,
                )
    
    if resp and not resp.get("error"):
        logger.info(f"Sucesso ao registrar inbound ({phone}) no GHL para tenant {location_id}.")
    else:
        logger.error(f"Falha ao transferir inbound ({phone}) para GHL no tenant {location_id}.")


@router.post("/inbound/{location_id}")
async def zapi_inbound_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    location_id: str = Path(..., description="O Location ID do GHL desta empresa"),
):
    """
    URL de Webhook que você vai colar no painel administrativo do Z-API:
    https://seu-servidor.com/webhook/zapi/inbound/{SEU_LOCATION_ID_DO_GHL}
    
    Ex: https://axenwp.meudominio.com/webhook/zapi/inbound/HjiMUOsCCHCjtxzEf8PR
    """
    try:
        payload = await request.json()
    except Exception as e:
        logger.error("Payload Z-API Inbound inválido.")
        return {"success": False, "error": "Invalid JSON"}

    # Segurança (opcional): Validação por Header, se configurado no Z-API
    # zapi_client_token = request.headers.get("Client-Token", "")
    # if settings.zapi_webhook_secret and zapi_client_token != settings.zapi_webhook_secret:
    #     logger.warning(f"Tentativa de acesso ao Webhook com token inválido.")
    #     return {"success": False, "error": "Unauthorized"}
    
    # Print do body pro dev (para debug inicial, depois remover ou colocar com model=debug)
    # logger.debug(f"Payload inbound: {payload}")

    # Envia pro processamento em background
    background_tasks.add_task(process_inbound_message, location_id, payload)
    
    # Se o GHL exigir 200 sempre, Z-API também precisa para parar de reenviar
    return {"success": True}


async def process_status_update(location_id: str, payload: Dict[str, Any]):
    """
    Processa webhooks de STATUS de mensagens (onMessageStatus) da Z-API
    e repassa pro GHL.
    Status esperados da Z-API: "DELIVERED", "READ", "ERROR", etc.
    """
    zapi_message_id = payload.get("messageId")
    status = payload.get("status", "").upper()
    
    if not zapi_message_id:
        return
        
    mapping = token_manager.get_ghl_message_id_by_zapi(zapi_message_id)
    if not mapping:
        logger.debug(f"Webhook de status ignorado: Z-API MessageId {zapi_message_id} não mapeado para GHL.")
        return
        
    ghl_message_id = mapping.get("ghl_message_id")
    
    # Traduzir status da Z-API para o GHL (delivered, read, failed)
    ghl_status = "delivered" # Default fallback seguro
    if status == "DELIVERED":
        ghl_status = "delivered"
    elif status == "READ":
        ghl_status = "read"
    elif status in ["ERROR", "FAILED", "REJECTED"]:
        ghl_status = "failed"
        
    logger.info(f"Atualizando status no GHL para '{ghl_status}' (GHL MsgId: {ghl_message_id})")
    await ghl_service.update_message_status(
        location_id=location_id,
        message_id=ghl_message_id,
        status=ghl_status,
        error_message=payload.get("error", "Erro remoto no Z-API") if ghl_status == "failed" else None
    )


@router.post("/status/{location_id}")
async def zapi_status_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    location_id: str = Path(..., description="O Location ID do GHL desta empresa"),
):
    """
    URL de Webhook (onMessageStatus) para colar no Z-API:
    https://seu-servidor.com/webhook/zapi/status/{SEU_LOCATION_ID_DO_GHL}
    """
    try:
        payload = await request.json()
    except Exception as e:
        logger.error("Payload Z-API Status inválido.")
        return {"success": False, "error": "Invalid JSON"}

    background_tasks.add_task(process_status_update, location_id, payload)
    
    return {"success": True}
