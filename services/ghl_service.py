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

    # Cache para os mapeamentos de Custom Fields por Location ID
    # formato: {"location_id": {"Nome do Campo": "id_do_campo"}}
    _custom_fields_cache = {}

    def __init__(self):
        self._client: httpx.AsyncClient | None = None

    async def startup(self):
        """Initialize the shared HTTP client. Call during app startup."""
        self._client = httpx.AsyncClient(timeout=30.0)

    async def shutdown(self):
        """Close the shared HTTP client. Call during app shutdown."""
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("GHLService.startup() was not called")
        return self._client

    def _resolve_ghl_location_id(self, location_id: str) -> str:
        """Retorna o ghl_location_id do tenant se existir, senão o location_id original."""
        tenant = token_manager.get_tenant(location_id)
        if tenant and getattr(tenant, 'ghl_location_id', None):
            return tenant.ghl_location_id
        return location_id

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
        direction: str = "inbound"
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
            "direction": direction,
        }

        if contact_id:
            payload["contactId"] = contact_id

        if provider_id:
            payload["conversationProviderId"] = provider_id

        if attachments:
            payload["attachments"] = attachments

        try:
            response = await self.client.post(
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
            response = await self.client.put(
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
            response = await self.client.get(
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

        ghl_loc = self._resolve_ghl_location_id(location_id)

        async def _do_search(query_phone: str):
            try:
                response = await self.client.get(
                    f"{self.BASE_URL}/contacts/search",
                    params={"query": query_phone, "locationId": ghl_loc},
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

        ghl_loc = self._resolve_ghl_location_id(location_id)
        first_name = name or phone
        payload = {
            "locationId": ghl_loc,
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
            response = await self.client.post(
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

    async def _get_custom_field_id_by_name(self, location_id: str, field_name: str) -> str | None:
        """Busca e faz cache do ID de um Custom Field com base no seu nome."""
        if location_id in self._custom_fields_cache and field_name in self._custom_fields_cache[location_id]:
            return self._custom_fields_cache[location_id][field_name]

        headers = await self._get_headers(location_id)
        if not headers:
            return None

        ghl_loc = self._resolve_ghl_location_id(location_id)
        try:
            response = await self.client.get(
                f"{self.BASE_URL}/locations/{ghl_loc}/customFields",
                headers=headers,
            )
            if response.status_code == 200:
                data = response.json()
                fields = data.get("customFields", [])

                if location_id not in self._custom_fields_cache:
                    self._custom_fields_cache[location_id] = {}

                for field in fields:
                    self._custom_fields_cache[location_id][field["name"]] = field["id"]

                return self._custom_fields_cache[location_id].get(field_name)
            else:
                logger.warning(f"Erro ao buscar Custom Fields da location {location_id}: {response.text}")
                return None
        except Exception as e:
            logger.error(f"Exceção ao buscar custom fields: {e}")
            return None

    async def get_pipelines(self, location_id: str) -> dict:
        """
        Lista os pipelines (oportunidades) da location.
        GET /opportunities/pipelines?locationId={locationId}
        Retorna dict com "pipelines" ou "error".
        """
        headers = await self._get_headers(location_id)
        if not headers:
            return {"error": True, "message": "Sem token válido"}

        ghl_loc = self._resolve_ghl_location_id(location_id)
        try:
            response = await self.client.get(
                f"{self.BASE_URL}/opportunities/pipelines",
                params={"locationId": ghl_loc},
                headers=headers,
            )
            if response.status_code == 200:
                data = response.json()
                return {"pipelines": data.get("pipelines", [])}
            else:
                body = response.text[:500]
                logger.error(f"Erro ao buscar pipelines: status={response.status_code}, body={body}")
                return {"error": True, "message": f"GHL API retornou {response.status_code}: {body}"}
        except Exception as e:
            logger.error(f"Exceção ao buscar pipelines: {e}")
            return {"error": True, "message": str(e)}

    async def get_custom_fields(self, location_id: str, model: str = "contact") -> dict:
        """
        Lista custom fields da location por modelo (contact ou opportunity).
        GET /locations/{locationId}/customFields?model={model}
        Retorna dict com "fields" ou "error".
        """
        headers = await self._get_headers(location_id)
        if not headers:
            return {"error": True, "message": "Sem token válido"}

        ghl_loc = self._resolve_ghl_location_id(location_id)
        try:
            # Buscar custom fields do modelo solicitado
            params = {}
            if model and model != "contact":
                params["model"] = model

            response = await self.client.get(
                f"{self.BASE_URL}/locations/{ghl_loc}/customFields",
                params=params,
                headers=headers,
            )
            if response.status_code == 200:
                data = response.json()
                fields = data.get("customFields", [])

                # Se pediu opportunity e veio vazio, buscar campos de contato como alternativa
                if not fields and model == "opportunity":
                    logger.info("Nenhum custom field de oportunidade encontrado. Buscando campos de contato...")
                    resp2 = await self.client.get(
                        f"{self.BASE_URL}/locations/{ghl_loc}/customFields",
                        headers=headers,
                    )
                    if resp2.status_code == 200:
                        fields = resp2.json().get("customFields", [])

                return {"fields": fields}
            else:
                body = response.text[:500]
                logger.error(f"Erro ao buscar custom fields ({model}): status={response.status_code}, body={body}")
                return {"error": True, "message": f"GHL API retornou {response.status_code}: {body}"}
        except Exception as e:
            logger.error(f"Exceção ao buscar custom fields ({model}): {e}")
            return {"error": True, "message": str(e)}

    async def create_opportunity(
        self,
        location_id: str,
        pipeline_id: str,
        stage_id: str,
        contact_id: str,
        name: str,
        custom_fields: list[dict] | None = None,
        notes: str | None = None,
        monetary_value: float = 0.0,
    ) -> dict | None:
        """
        Cria uma oportunidade (negócio) no GHL.
        POST /opportunities/
        """
        headers = await self._get_headers(location_id)
        if not headers:
            return None

        ghl_loc = self._resolve_ghl_location_id(location_id)
        payload = {
            "pipelineId": pipeline_id,
            "pipelineStageId": stage_id,
            "locationId": ghl_loc,
            "contactId": contact_id,
            "name": name,
            "status": "open",
            "monetaryValue": monetary_value,
        }

        if custom_fields:
            payload["customFields"] = custom_fields

        try:
            response = await self.client.post(
                f"{self.BASE_URL}/opportunities/",
                json=payload,
                headers=headers,
            )

            if response.status_code in (200, 201):
                data = response.json()
                opp_id = data.get("opportunity", {}).get("id", "?")
                logger.info(f"Oportunidade criada no GHL: id={opp_id}, contact={contact_id}, location={location_id}")

                # Adicionar notas separadamente se fornecidas
                if notes and opp_id != "?":
                    await self._add_opportunity_notes(location_id, opp_id, notes)

                return data.get("opportunity", data)
            else:
                body_data = {}
                try:
                    body_data = response.json()
                except:
                    body_data = {"text": response.text}
                logger.error(f"Erro ao criar oportunidade: status={response.status_code}, body={response.text}")
                return {"error": True, "status_code": response.status_code, "body": body_data}
        except Exception as e:
            logger.error(f"Exceção ao criar oportunidade: {e}")
            return {"error": True, "status_code": 500, "body": {"message": str(e)}}

    async def _add_opportunity_notes(self, location_id: str, opportunity_id: str, notes: str) -> bool:
        """Adiciona notas a uma oportunidade existente via PUT."""
        headers = await self._get_headers(location_id)
        if not headers:
            return False

        try:
            response = await self.client.put(
                f"{self.BASE_URL}/opportunities/{opportunity_id}",
                json={"notes": notes},
                headers=headers,
            )
            if response.status_code == 200:
                logger.info(f"Notas adicionadas à oportunidade {opportunity_id}")
                return True
            else:
                logger.warning(f"Erro ao adicionar notas à oportunidade: {response.text}")
                return False
        except Exception as e:
            logger.error(f"Exceção ao adicionar notas: {e}")
            return False

    async def update_contact(self, location_id: str, contact_id: str, data: dict) -> dict | None:
        """
        Atualiza um contato no GHL (custom fields, nome, etc).
        PUT /contacts/{contactId}
        """
        headers = await self._get_headers(location_id)
        if not headers:
            return None

        try:
            response = await self.client.put(
                f"{self.BASE_URL}/contacts/{contact_id}",
                json=data,
                headers=headers,
            )
            if response.status_code == 200:
                result = response.json()
                logger.info(f"Contato {contact_id} atualizado no GHL")
                return result.get("contact", result)
            else:
                logger.error(f"Erro ao atualizar contato {contact_id}: status={response.status_code}, body={response.text}")
                return None
        except Exception as e:
            logger.error(f"Exceção ao atualizar contato: {e}")
            return None

    async def is_ai_active_for_contact(self, location_id: str, contact_id: str) -> bool:
        """
        Verifica se o contato tem o Custom Field 'Status IA' com o valor 'Ativada'.
        """
        field_id = await self._get_custom_field_id_by_name(location_id, "Status IA")
        if not field_id:
            logger.debug(f"Custom Field 'Status IA' não encontrado na location {location_id}.")
            return False
            
        contact = await self.get_contact(location_id, contact_id)
        if not contact:
            return False
            
        custom_fields = contact.get("customFields", [])
        for cf in custom_fields:
            if cf.get("id") == field_id:
                # O valor pode ser uma lista dependendo do tipo do campo, ou uma string.
                val = cf.get("value")
                if isinstance(val, list):
                    return "Ativada" in val
                elif isinstance(val, str):
                    return val.strip().lower() == "ativada"
                    
        return False



# Instância global
ghl_service = GHLService()
