"""
Recebe webhooks do GoHighLevel (Outbound).
Quando o usuário envia uma mensagem no CRM usando o Axen WP como provider,
este webhook recebe a mensagem e a repassa para a Z-API.
"""

from fastapi import APIRouter, Request, BackgroundTasks, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from typing import Optional, List
import re
import urllib.parse
from pydantic import Field

from utils.logger import logger
from auth.token_manager import token_manager
from channels.registry import resolve_send_adapter
from services.channel_policy import provider_label
from services.ghl_service import ghl_service
from services.message_log import message_type_from_url, persist_message


router = APIRouter(prefix="/webhook/ghl", tags=["Webhooks GHL"])


class GHLOutboundPayload(BaseModel):
    """Schema do payload recebido do GHL (Conversation Provider)."""

    contactId: Optional[str] = Field(None, alias="contact_id")
    locationId: str = Field("", alias="location_id")
    messageId: str = Field("", alias="message_id")
    type: Optional[str] = None
    status: Optional[str] = None
    phone: Optional[str] = None
    message: Optional[str] = ""
    attachments: Optional[List[str]] = []
    userId: Optional[str] = Field(None, alias="user_id")

    def __init__(self, **data):
        # Allow accepting both camelCase and snake_case directly
        if 'contactId' in data and 'contact_id' not in data:
            data['contact_id'] = data['contactId']
        if 'locationId' in data and 'location_id' not in data:
            data['location_id'] = data['locationId']
        if 'messageId' in data and 'message_id' not in data:
            data['message_id'] = data['messageId']
        if 'userId' in data and 'user_id' not in data:
            data['user_id'] = data['userId']
        super().__init__(**data)
    
    class Config:
        extra = "allow"


async def process_outbound_message(payload: GHLOutboundPayload):
    """
    Processamento em background da mensagem outbound.
    1. Acha a instância Z-API configurada para este location_id.
    2. Envia para a Z-API.
    3. Confirma o status no GHL.
    """
    location_id = payload.locationId
    tenant = token_manager.get_tenant(location_id)

    # GHL enviará múltiplos webhooks: "pending", "sent" (quando criamos) e "delivered" (quando atualizamos a msg). 
    # Só processamos as mensagens novas que precisam ir para a Z-API.
    if payload.status and payload.status.lower() not in ["pending", "sent"]:
        logger.debug(f"Ignorando GHL Outbound (status={payload.status}) - ID: {payload.messageId}")
        return

    if not tenant:
        logger.error(f"GHL Outbound abortado: Tenant {location_id} não encontrado/registrado.")
        await ghl_service.update_message_status(
            location_id, payload.messageId, status="failed", error_message="Empresa não cadastrada no servidor."
        )
        return

    if not getattr(tenant, 'is_active', True):
        logger.info(f"GHL Outbound abortado: Automação pausada/desativada para {location_id}.")
        await ghl_service.update_message_status(
            location_id, payload.messageId, status="failed", error_message="Automação Axen WP pausada no painel."
        )
        return

    # O envio segue o provedor ATIVO da instância (WAHA ou Z-API), resolvido pela
    # mesma política que a exclusividade usa — a credencial Z-API que ficou
    # dormente após uma troca não despacha nada, senão sairia pelo número errado
    # e ainda seria marcada como entregue no CRM. Sem provedor ativo, a mensagem
    # falha com motivo legível no CRM em vez de sumir.
    adapter = resolve_send_adapter(tenant)
    if adapter is None:
        logger.error(f"GHL Outbound abortado: nenhum provedor de WhatsApp conectado em {location_id}.")
        await ghl_service.update_message_status(
            location_id, payload.messageId, status="failed",
            error_message="Nenhum provedor de WhatsApp conectado nesta instância.",
        )
        return

    if not adapter.credentials_ok(tenant):
        rotulo = provider_label(adapter.provider)
        logger.error(f"GHL Outbound abortado: {rotulo} sem credencial completa em {location_id}.")
        await ghl_service.update_message_status(
            location_id, payload.messageId, status="failed",
            error_message=f"{rotulo} não está configurado nesta instância.",
        )
        return

    phone = payload.phone
    
    if not phone and payload.contactId:
        logger.info(f"Telefone não enviado no payload. Buscando contato {payload.contactId} no GHL...")
        contact_data = await ghl_service.get_contact(location_id, payload.contactId)
        if contact_data:
            # GHL armazena os telefones em contact_data.get("phone") ou "phone" normal, ou "phoneDnc"
            phone = contact_data.get("phone") or contact_data.get("phone1")
            
    if not phone:
        logger.error(f"GHL Outbound abortado: Telefone não encontrado para o contato {payload.contactId}.")
        await ghl_service.update_message_status(
            location_id, payload.messageId, status="failed", error_message="Telefone do contato inválido ou não encontrado."
        )
        return
        
    payload_dict = payload.dict()
    message_text = payload.message or payload_dict.get("body", "")

    logger.info(
        f"[CHANNEL] Enviando via {provider_label(adapter.provider)} para {tenant.company_name}: "
        f"phone={phone}, msg_id={payload.messageId}"
    )

    success = False

    def _generate_clean_filename(url: str, caption: str) -> str:
        parsed = urllib.parse.urlparse(url)
        base_name = parsed.path.split('/')[-1] if parsed.path else "documento"
        ext = base_name.split('.')[-1] if '.' in base_name else "pdf"
        
        # Consider a name "ugly" if it's very long (like a GHL UUID)
        is_ugly = len(base_name) > 25
        
        # If user typed a short message like "segue cnpj", use it!
        if caption and len(caption) < 60:
            clean_caption = re.sub(r'[^\w\s-]', '', caption).strip()
            if clean_caption:
                return f"{clean_caption}.{ext}"
                
        # If it's a UUID and there's no caption, use nice defaults
        if is_ugly:
            if ext.lower() in ['pdf', 'doc', 'docx', 'txt']:
                return f"Documento.{ext}"
            elif ext.lower() in ['xls', 'xlsx', 'csv']:
                return f"Planilha.{ext}"
            elif ext.lower() in ['png', 'jpg', 'jpeg']:
                return f"Imagem.{ext}"
            else:
                return f"Arquivo.{ext}"
                
        return base_name

    enviados: list = []  # provider_message_ids dos envios desta mensagem do operador

    def _map(res) -> bool:
        """Guarda o vínculo id-do-provedor ↔ id-do-CRM; sem ele o status nunca sobe."""
        if res and res.ok and res.provider_message_id:
            token_manager.save_message_mapping(res.provider_message_id, payload.messageId, location_id)
            enviados.append(res.provider_message_id)
        return bool(res and res.ok)

    try:
        # Se tiver anexos, priorizamos o envio do primeiro anexo.
        # Caso clássico: envio de imagem com caption.
        if payload.attachments and len(payload.attachments) > 0:
            attachment_url = payload.attachments[0]
            # Usa send_image ou send_document dependendo da URL
            if any(ext in attachment_url.lower() for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp"]):
                res = await adapter.send_image(tenant, phone, attachment_url, caption=message_text)
            elif any(ext in attachment_url.lower() for ext in [".mp3", ".ogg", ".wav", ".mpeg"]):
                res = await adapter.send_audio(tenant, phone, attachment_url)
                # Se tinha texto junto com o áudio GHL (raro, mas pode ocorrer), enviamos logo depois
                if message_text:
                    _map(await adapter.send_text(tenant, phone, message_text))
            else:
                res = await adapter.send_document(
                    tenant, phone, attachment_url,
                    filename=_generate_clean_filename(attachment_url, message_text),
                )
                # Se tinha texto junto, manda
                if message_text:
                    _map(await adapter.send_text(tenant, phone, message_text))

            # (Opcional) se houver múltiplos anexos, poderia fazer um forloop
            success = _map(res)

        # Se não tem anexos, é texto simples (Mas pode ser um Link do GHL para arquivo grande)
        elif message_text:
            # GHL envia arquivos pesados (>5MB) como links no corpo do texto. Ex: https://api.leadconnectorhq.com/l/YE...
            # Vamos procurar se a mensagem inteira é apenas um link ou se contém um link de arquivo
            url_pattern = r'(https?://(?:api\.leadconnectorhq\.com|storage\.googleapis\.com)[^\s]+)'
            match = re.search(url_pattern, message_text)

            if match:
                file_url = match.group(1)

                # Se a mensagem for APENAS o link (GHL envia nativamente assim)
                if file_url == message_text.strip():
                    success = _map(await adapter.send_document(
                        tenant, phone, file_url, filename=_generate_clean_filename(file_url, "")
                    ))
                else:
                    # Se tiver texto misturado, manda os dois (Texto e Link) como texto normal
                    # ou poderia mandar preview de link. Por precaução mantemos texto.
                    success = _map(await adapter.send_text(tenant, phone, message_text))
            else:
                # Texto 100% normal sem links do GHL
                success = _map(await adapter.send_text(tenant, phone, message_text))

        # Log da mensagem que o OPERADOR digitou no CRM (base do painel próprio).
        # Dedup por ghl_message_id (o GHL dispara pending+sent para o mesmo id).
        anexo = payload.attachments[0] if payload.attachments else None
        await persist_message(
            location_id=location_id, channel="whatsapp", provider=adapter.provider,
            direction="outbound", sender_role="operator_crm", contact_ref=phone,
            ghl_contact_id=payload.contactId, text=message_text or None,
            message_type=message_type_from_url(anexo), media_url=anexo,
            provider_message_id=enviados[-1] if enviados else None,
            ghl_message_id=payload.messageId,
            status="sent" if success else "failed",
        )

        # Atualiza status no GHL
        if success:
            await ghl_service.update_message_status(
                location_id, payload.messageId, status="delivered"
            )
        else:
            await ghl_service.update_message_status(
                location_id, payload.messageId, status="failed", error_message=f"Falha no envio via {provider_label(adapter.provider)}."
            )

    except Exception as e:
        logger.error(f"Erro ao processar outbound para {location_id}: {e}")
        await ghl_service.update_message_status(
            location_id, payload.messageId, status="failed", error_message=str(e)
        )


@router.post("/outbound")
async def ghl_outbound_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Endpoint que o GHL chama quando uma mensagem é enviada pela interface utilizando
    o provedor customizado.
    Sempre retornamos 200 rápido e processamos no background para evitar timeouts da UI do GHL.
    """
    try:
        payload_dict = await request.json()
        logger.info(f"PAYLOAD BRUTO RECEBIDO DO GHL: {payload_dict}")
        
        # Faz parse ignorando erros super estritos
        payload = GHLOutboundPayload(**payload_dict)
    except Exception as e:
        logger.error(f"Erro ao capturar JSON do GHL: {e}")
        return {"success": False, "error": str(e)}

    logger.info(f"Recebido GHL Outbound (location={payload.locationId}, phone={payload.phone})")
    
    # Enfileira a tarefa
    background_tasks.add_task(process_outbound_message, payload)
    
    return {"success": True, "message": "Enfileirado para envio Z-API"}
