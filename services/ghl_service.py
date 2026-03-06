"""
Cliente HTTP para a API do GoHighLevel.
Gerencia envio de mensagens inbound, atualização de status e contatos.
"""

import httpx

from utils.config import settings
from utils.logger import logger
from auth.token_manager import token_manager


class GHLService:
    """Serviço para interagir com a API do GoHighLevel."""

    BASE_URL = settings.ghl_api_base

    async def _get_headers(self, location_id: str) -> dict | None:
        """Monta os headers com o token válido do tenant."""
        token = await token_manager.get_valid_token(location_id)
        if not token:
            logger.error(f"Sem token válido para location {location_id}")
            return None
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Version": "2021-07-28",
        }

    async def send_inbound_message(
        self,
        location_id: str,
        phone: str,
        message: str,
        attachments: list[str] | None = None,
        conversation_provider_id: str = "",
        contact_id: str | None = None,
    ) -> dict | None:
        """
        Registra uma mensagem inbound (recebida do WhatsApp) no CRM.
        POST /conversations/messages/inbound
        """
        headers = await self._get_headers(location_id)
        if not headers:
            return None

        tenant = token_manager.get_tenant(location_id)
        provider_id = conversation_provider_id or (
            tenant.conversation_provider_id if tenant else ""
        )

        # Formatar telefone no padrão internacional (com +)
        formatted_phone = phone.strip()
        if not formatted_phone.startswith("+"):
            formatted_phone = f"+{formatted_phone}"

        payload = {
            "type": "SMS",
            "locationId": location_id,
            "phone": formatted_phone,
            "message": message,
            "direction": "inbound",
        }

        if contact_id:
            payload["contactId"] = contact_id

        if provider_id:
            payload["conversationProviderId"] = provider_id

        if attachments:
            payload["attachments"] = attachments

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.BASE_URL}/conversations/messages/inbound",
                    json=payload,
                    headers=headers,
                )

                if response.status_code in (200, 201):
                    data = response.json()
                    logger.info(
                        f"Mensagem inbound registrada no GHL: "
                        f"phone={formatted_phone}, location={location_id}"
                    )
                    return data
                else:
                    body_data = {}
                    try:
                        body_data = response.json()
                    except:
                        body_data = {"text": response.text}

                    logger.error(
                        f"Erro ao registrar inbound no GHL: "
                        f"status={response.status_code}, body={response.text}"
                    )
                    return {"error": True, "status_code": response.status_code, "body": body_data}

        except Exception as e:
            logger.error(f"Exceção ao enviar inbound para GHL: {e}")
            return {"error": True, "status_code": 500, "body": {"message": str(e)}}

    async def update_message_status(
        self,
        location_id: str,
        message_id: str,
        status: str = "delivered",
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> bool:
        """
        Atualiza o status de uma mensagem outbound.
        PUT /conversations/messages/{messageId}/status
        """
        headers = await self._get_headers(location_id)
        if not headers:
            return False

        payload = {"status": status}
        if error_code or error_message:
            payload["error"] = {
                "code": error_code or "API_ERROR",
                "message": error_message or "Unknown error"
            }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.put(
                    f"{self.BASE_URL}/conversations/messages/{message_id}/status",
                    json=payload,
                    headers=headers,
                )

                if response.status_code == 200:
                    logger.info(
                        f"Status da mensagem {message_id} atualizado para '{status}'"
                    )
                    return True
                else:
                    logger.error(
                        f"Erro ao atualizar status da mensagem: "
                        f"status={response.status_code}, body={response.text}"
                    )
                    return False

        except Exception as e:
            logger.error(f"Exceção ao atualizar status de mensagem: {e}")
            return False

    async def get_contact(self, location_id: str, contact_id: str) -> dict | None:
        """
        Busca um contato pelo ID para descobrir o telefone.
        GET /contacts/{contactId}
        """
        headers = await self._get_headers(location_id)
        if not headers:
            return None

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{self.BASE_URL}/contacts/{contact_id}",
                    headers=headers,
                )

                if response.status_code == 200:
                    data = response.json()
                    return data.get("contact", {})
                else:
                    logger.warning(
                        f"Busca de contato por ID retornou status {response.status_code}: {response.text}"
                    )
                    return None
        except Exception as e:
            logger.error(f"Exceção ao buscar contato por ID: {e}")
            return None

    async def search_contact_by_phone(
        self, location_id: str, phone: str
    ) -> dict | None:
        """
        Busca um contato pelo telefone.
        Tenta buscar o número exato. Se for do Brasil (+55) e tiver DDD, 
        tenta buscar a variação com/sem o 9º dígito para evitar duplicidade.
        GET /contacts/search?query={phone}&locationId={locationId}
        """
        headers = await self._get_headers(location_id)
        if not headers:
            return None

        async def _do_search(query_phone: str):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(
                        f"{self.BASE_URL}/contacts/search",
                        params={"query": query_phone, "locationId": location_id},
                        headers=headers,
                    )
                    if response.status_code == 200:
                        data = response.json()
                        contacts = data.get("contacts", [])
                        if contacts:
                            return contacts[0]
                    return None
            except Exception as e:
                logger.error(f"Exceção ao buscar contato ({query_phone}): {e}")
                return None

        # 1. Garante que o número comece com '+' para a API do GHL entender que é um telefone
        clean_phone = phone.strip()
        if not clean_phone.startswith("+"):
            clean_phone = f"+{clean_phone}"

        contact = await _do_search(clean_phone)
        if contact:
            return contact

        # 2. Se for Brasil (+55), tenta a variação do 9º dígito
        if clean_phone.startswith("+55") and len(clean_phone) in (13, 14):
            # +55 (3 chars) + DDD (2 chars) + Número (8 ou 9 chars)
            ddd = clean_phone[3:5]
            numero = clean_phone[5:]
            
            alt_phone = None
            if len(numero) == 9 and numero.startswith("9"):
                # Tem 9, vamos buscar sem o 9
                alt_phone = f"+55{ddd}{numero[1:]}"
            elif len(numero) == 8:
                # Não tem 9, vamos buscar com o 9
                alt_phone = f"+55{ddd}9{numero}"

            if alt_phone:
                logger.info(f"Contato não encontrado com {clean_phone}. Tentando variação BR: {alt_phone}")
                contact_alt = await _do_search(alt_phone)
                if contact_alt:
                    return contact_alt

        return None

    async def create_contact(
        self, location_id: str, phone: str, name: str = "", email: str = ""
    ) -> dict | None:
        """
        Cria um novo contato no GHL.
        Se for um @lid, não enviamos como "phone" para não dar erro de formatação na API.
        POST /contacts/
        """
        headers = await self._get_headers(location_id)
        if not headers:
            return None

        first_name = name or phone
        payload = {
            "locationId": location_id,
            "firstName": first_name,
        }
        
        # Só envia 'phone' se for um número de verdade
        if "@lid" not in phone:
            formatted_phone = phone.strip()
            if not formatted_phone.startswith("+"):
                formatted_phone = f"+{formatted_phone}"
            payload["phone"] = formatted_phone

        if email:
            payload["email"] = email

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.BASE_URL}/contacts/",
                    json=payload,
                    headers=headers,
                )
                if response.status_code in (200, 201):
                    data = response.json()
                    logger.info(f"Novo contato criado no GHL: {phone}")
                    return data.get("contact", {})
                else:
                    logger.error(f"Erro ao criar contato {phone}: {response.text}")
                    return None
        except Exception as e:
            logger.error(f"Exceção ao criar contato: {e}")
            return None


# Instância global
ghl_service = GHLService()
