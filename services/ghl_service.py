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

                if response.status_code == 200:
                    data = response.json()
                    logger.info(
                        f"Mensagem inbound registrada no GHL: "
                        f"phone={formatted_phone}, location={location_id}"
                    )
                    return data
                else:
                    logger.error(
                        f"Erro ao registrar inbound no GHL: "
                        f"status={response.status_code}, body={response.text}"
                    )
                    return None

        except Exception as e:
            logger.error(f"Exceção ao enviar inbound para GHL: {e}")
            return None

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
        GET /contacts/search?query={phone}&locationId={locationId}
        """
        headers = await self._get_headers(location_id)
        if not headers:
            return None

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{self.BASE_URL}/contacts/search",
                    params={"query": phone, "locationId": location_id},
                    headers=headers,
                )

                if response.status_code == 200:
                    data = response.json()
                    contacts = data.get("contacts", [])
                    if contacts:
                        return contacts[0]
                    return None
                else:
                    logger.warning(
                        f"Busca de contato retornou status {response.status_code}"
                    )
                    return None

        except Exception as e:
            logger.error(f"Exceção ao buscar contato: {e}")
            return None

    async def create_contact(
        self, location_id: str, phone: str, name: str = "", email: str = ""
    ) -> dict | None:
        """
        Cria um novo contato no GHL.
        POST /contacts/
        """
        headers = await self._get_headers(location_id)
        if not headers:
            return None

        formatted_phone = phone.strip()
        if not formatted_phone.startswith("+"):
            formatted_phone = f"+{formatted_phone}"

        first_name = name or formatted_phone
        payload = {
            "locationId": location_id,
            "phone": formatted_phone,
            "firstName": first_name,
        }
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
                    logger.info(f"Novo contato criado no GHL: {formatted_phone}")
                    return data.get("contact", {})
                else:
                    logger.error(f"Erro ao criar contato {formatted_phone}: {response.text}")
                    return None
        except Exception as e:
            logger.error(f"Exceção ao criar contato: {e}")
            return None


# Instância global
ghl_service = GHLService()
