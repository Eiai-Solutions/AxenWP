"""
Cliente HTTP para a API Z-API.
Gerencia envio de mensagens de texto, imagem, documento e áudio via WhatsApp.
"""

import httpx

from utils.logger import logger


class ZAPIService:
    """Serviço para interagir com a API Z-API."""

    BASE_URL = "https://api.z-api.io"

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
            raise RuntimeError("ZAPIService.startup() was not called")
        return self._client

    def _build_url(self, instance_id: str, token: str, endpoint: str) -> str:
        """Monta a URL completa do endpoint Z-API."""
        return f"{self.BASE_URL}/instances/{instance_id}/token/{token}/{endpoint}"

    async def get_webhook_received(self, instance_id: str, token: str, client_token: str = "") -> dict | None:
        """Lê a URL de webhook 'on-receive' atualmente configurada na Z-API."""
        try:
            resp = await self.client.get(
                self._build_url(instance_id, token, "webhook-received"),
                headers=self._get_headers(client_token),
            )
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f"webhook-received GET retornou {resp.status_code}: {resp.text[:200]}")
            return None
        except Exception as e:
            logger.error(f"Erro ao ler webhook Z-API: {e}")
            return None

    async def set_webhook_received(
        self, instance_id: str, token: str, webhook_url: str, client_token: str = ""
    ) -> bool:
        """Registra a URL de webhook 'on-receive' na Z-API."""
        try:
            resp = await self.client.put(
                self._build_url(instance_id, token, "update-webhook-received"),
                headers=self._get_headers(client_token),
                json={"value": webhook_url},
            )
            if resp.status_code == 200:
                logger.info(f"Z-API webhook-received configurado: {webhook_url}")
                return True
            logger.error(f"Falha set webhook Z-API: {resp.status_code} {resp.text[:200]}")
            return False
        except Exception as e:
            logger.error(f"Exception set webhook Z-API: {e}")
            return False

    def _get_headers(self, client_token: str = "") -> dict:
        """Headers padrão para chamadas Z-API."""
        headers = {"Content-Type": "application/json"}
        if client_token:
            headers["Client-Token"] = client_token
        return headers

    async def send_text(
        self,
        instance_id: str,
        token: str,
        phone: str,
        message: str,
        client_token: str = "",
        delay_typing: int = 0,
    ) -> dict | None:
        """
        Envia uma mensagem de texto simples.
        POST /instances/{id}/token/{token}/send-text
        """
        url = self._build_url(instance_id, token, "send-text")
        payload = {
            "phone": self._format_phone(phone),
            "message": message,
        }
        if delay_typing:
            payload["delayTyping"] = delay_typing

        return await self._post(url, payload, client_token)

    async def send_image(
        self,
        instance_id: str,
        token: str,
        phone: str,
        image_url: str,
        caption: str = "",
        client_token: str = "",
    ) -> dict | None:
        """
        Envia uma imagem.
        POST /instances/{id}/token/{token}/send-image
        """
        url = self._build_url(instance_id, token, "send-image")
        payload = {
            "phone": self._format_phone(phone),
            "image": image_url,
        }
        if caption:
            payload["caption"] = caption

        return await self._post(url, payload, client_token)

    async def send_document(
        self,
        instance_id: str,
        token: str,
        phone: str,
        document_url: str,
        filename: str = "document",
        client_token: str = "",
    ) -> dict | None:
        """
        Envia um documento/arquivo.
        POST /instances/{id}/token/{token}/send-document/{extension}
        """
        # Extrair extensão do filename
        ext = filename.rsplit(".", 1)[-1] if "." in filename else "pdf"
        url = self._build_url(instance_id, token, f"send-document/{ext}")
        payload = {
            "phone": self._format_phone(phone),
            "document": document_url,
            "fileName": filename,
        }

        return await self._post(url, payload, client_token)

    async def send_audio(
        self,
        instance_id: str,
        token: str,
        phone: str,
        audio_url: str,
        client_token: str = "",
        record_audio: bool = True,
    ) -> dict | None:
        """
        Envia um áudio como mensagem de voz (PTT) — formato nativo do WhatsApp,
        com ondulação (waveform), velocidade e ícone de microfone.

        POST /instances/{id}/token/{token}/send-audio
        """
        url = self._build_url(instance_id, token, "send-audio")
        payload = {
            "phone": self._format_phone(phone),
            "audio": audio_url,
            # 'recordAudio' é o parâmetro que sinaliza PTT (mensagem de voz)
            "recordAudio": record_audio,
            # 'waveform' (parâmetro mais recente da Z-API) gera a ondulação visual
            "waveform": record_audio,
            # 'viewOnce' false garante que não vai ser mensagem de uma vez só
            "viewOnce": False,
        }

        return await self._post(url, payload, client_token)

    async def send_link(
        self,
        instance_id: str,
        token: str,
        phone: str,
        message: str,
        link_url: str,
        title: str = "",
        description: str = "",
        image_url: str = "",
        client_token: str = "",
    ) -> dict | None:
        """
        Envia uma mensagem com preview de link.
        POST /instances/{id}/token/{token}/send-link
        """
        url = self._build_url(instance_id, token, "send-link")
        payload = {
            "phone": self._format_phone(phone),
            "message": message,
            "linkUrl": link_url,
        }
        if title:
            payload["title"] = title
        if description:
            payload["description"] = description
        if image_url:
            payload["image"] = image_url

        return await self._post(url, payload, client_token)

    async def _post(
        self, url: str, payload: dict, client_token: str = ""
    ) -> dict | None:
        """Executa um POST genérico para a Z-API."""
        try:
            response = await self.client.post(
                url,
                json=payload,
                headers=self._get_headers(client_token),
            )

            if response.status_code in (200, 201):
                data = response.json()
                logger.info(
                    f"Z-API enviou mensagem com sucesso: "
                    f"phone={payload.get('phone')}, zapiMessageId={data.get('zapiMessageId', 'N/A')}"
                )
                return data
            else:
                logger.error(
                    f"Erro Z-API: status={response.status_code}, "
                    f"url={url}, body={response.text}"
                )
                return None

        except Exception as e:
            logger.error(f"Exceção ao chamar Z-API: {e}")
            return None

    @staticmethod
    def _format_phone(phone: str) -> str:
        """
        Formata o telefone para o padrão Z-API (apenas números, sem + ou espaços).
        Ex: '+5511999999999' → '5511999999999'
        """
        return "".join(c for c in phone if c.isdigit())

    async def get_status(
        self, instance_id: str, token: str, client_token: str = ""
    ) -> dict | None:
        """
        Retorna o status de conexão da instância (CONNECTED, DISCONNECTED, etc).
        GET /instances/{id}/token/{token}/status
        """
        url = self._build_url(instance_id, token, "status")
        try:
            response = await self.client.get(url, headers=self._get_headers(client_token), timeout=10.0)
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.error(f"Erro ao buscar status Z-API: {e}")
        return None

    async def get_qr_code(
        self, instance_id: str, token: str, client_token: str = ""
    ) -> dict | None:
        """
        Solicita um novo QR Code para reconexão.
        GET /instances/{id}/token/{token}/qr-code/image
        """
        url = self._build_url(instance_id, token, "qr-code/image")
        try:
            response = await self.client.get(url, headers=self._get_headers(client_token), timeout=15.0)
            if response.status_code == 200:
                return response.json()  # Retorna {"value": "data:image/png;base64,..."}
        except Exception as e:
            logger.error(f"Erro ao buscar QR Code Z-API: {e}")
        return None


# Instância global
zapi_service = ZAPIService()
