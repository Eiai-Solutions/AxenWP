"""
Gerenciamento de tokens OAuth do GoHighLevel.
Persiste tokens em arquivos JSON e renova automaticamente quando expiram.
"""

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import httpx

from utils.logger import logger
from utils.config import settings

# Diretório onde os JSONs dos tenants são salvos
TENANTS_DIR = Path(__file__).parent.parent / "data" / "tenants"
TENANTS_DIR.mkdir(parents=True, exist_ok=True)


class TenantData:
    """Representa os dados persistidos de um tenant (empresa)."""

    def __init__(self, data: dict):
        self.location_id: str = data.get("location_id", "")
        self.company_name: str = data.get("company_name", "")
        self.client_id: str = data.get("client_id", settings.ghl_client_id)
        self.client_secret: str = data.get("client_secret", settings.ghl_client_secret)
        self.access_token: str = data.get("access_token", "")
        self.refresh_token: str = data.get("refresh_token", "")
        self.token_expires_at: str = data.get("token_expires_at", "")
        self.zapi_instance_id: str = data.get("zapi_instance_id", "")
        self.zapi_token: str = data.get("zapi_token", "")
        self.zapi_client_token: str = data.get("zapi_client_token", "")
        self.conversation_provider_id: str = data.get(
            "conversation_provider_id",
            settings.ghl_conversation_provider_id,
        )
        self.created_at: str = data.get("created_at", "")

    def to_dict(self) -> dict:
        return {
            "location_id": self.location_id,
            "company_name": self.company_name,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "token_expires_at": self.token_expires_at,
            "zapi_instance_id": self.zapi_instance_id,
            "zapi_token": self.zapi_token,
            "zapi_client_token": self.zapi_client_token,
            "conversation_provider_id": self.conversation_provider_id,
            "created_at": self.created_at,
        }

    @property
    def is_token_expired(self) -> bool:
        """Verifica se o access_token está expirado ou prestes a expirar (margem de 1h)."""
        if not self.token_expires_at:
            return True
        try:
            expires = datetime.fromisoformat(self.token_expires_at.replace("Z", "+00:00"))
            margin = timedelta(hours=1)
            return datetime.now(timezone.utc) >= (expires - margin)
        except (ValueError, TypeError):
            return True


class TokenManager:
    """Gerencia tokens de todos os tenants."""

    def __init__(self):
        self._cache: dict[str, TenantData] = {}
        self._load_all()

    def _load_all(self):
        """Carrega todos os tenants do disco."""
        count = 0
        for filepath in TENANTS_DIR.glob("*.json"):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                tenant = TenantData(data)
                self._cache[tenant.location_id] = tenant
                count += 1
            except Exception as e:
                logger.error(f"Erro ao carregar tenant {filepath.name}: {e}")
        logger.info(f"TokenManager: {count} tenant(s) carregado(s)")

    def _save_tenant(self, tenant: TenantData):
        """Salva os dados de um tenant no disco."""
        filepath = TENANTS_DIR / f"{tenant.location_id}.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(tenant.to_dict(), f, indent=2, ensure_ascii=False)
        logger.info(f"Tenant {tenant.company_name} ({tenant.location_id}) salvo em disco")

    def get_tenant(self, location_id: str) -> Optional[TenantData]:
        """Retorna o tenant pelo location_id."""
        return self._cache.get(location_id)

    def get_all_tenants(self) -> list[TenantData]:
        """Retorna todos os tenants carregados."""
        return list(self._cache.values())

    def register_tenant(
        self,
        location_id: str,
        company_name: str,
        access_token: str,
        refresh_token: str,
        expires_in: int,
        client_id: str = "",
        client_secret: str = "",
        **extras,
    ) -> TenantData:
        """Registra (ou atualiza) um tenant com os dados do OAuth."""
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

        existing = self._cache.get(location_id)
        data = existing.to_dict() if existing else {}

        data.update(
            {
                "location_id": location_id,
                "company_name": company_name or data.get("company_name", ""),
                "client_id": client_id or data.get("client_id", settings.ghl_client_id),
                "client_secret": client_secret
                or data.get("client_secret", settings.ghl_client_secret),
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_expires_at": expires_at.isoformat(),
                "created_at": data.get(
                    "created_at", datetime.now(timezone.utc).isoformat()
                ),
            }
        )
        data.update(extras)

        tenant = TenantData(data)
        self._cache[location_id] = tenant
        self._save_tenant(tenant)
        return tenant

    async def get_valid_token(self, location_id: str) -> Optional[str]:
        """
        Retorna um access_token válido.
        Se estiver expirado, faz o refresh automaticamente.
        """
        tenant = self.get_tenant(location_id)
        if not tenant:
            logger.error(f"Tenant {location_id} não encontrado")
            return None

        if not tenant.is_token_expired:
            return tenant.access_token

        # Precisa renovar
        logger.info(f"Token expirado para {tenant.company_name}, renovando...")
        success = await self._refresh_token(tenant)
        if success:
            return tenant.access_token

        logger.error(f"Falha ao renovar token para {tenant.company_name}")
        return None

    async def _refresh_token(self, tenant: TenantData) -> bool:
        """Faz o refresh do access_token via API GHL."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{settings.ghl_api_base}/oauth/token",
                    data={
                        "client_id": tenant.client_id,
                        "client_secret": tenant.client_secret,
                        "grant_type": "refresh_token",
                        "refresh_token": tenant.refresh_token,
                        "user_type": "Location",
                        "redirect_uri": settings.ghl_redirect_uri,
                    },
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                )

                if response.status_code != 200:
                    logger.error(
                        f"Refresh falhou para {tenant.company_name}: "
                        f"status={response.status_code}, body={response.text}"
                    )
                    return False

                data = response.json()
                expires_at = datetime.now(timezone.utc) + timedelta(
                    seconds=data.get("expires_in", 86399)
                )

                tenant.access_token = data["access_token"]
                tenant.refresh_token = data["refresh_token"]
                tenant.token_expires_at = expires_at.isoformat()

                self._cache[tenant.location_id] = tenant
                self._save_tenant(tenant)

                logger.info(f"Token renovado com sucesso para {tenant.company_name}")
                return True

        except Exception as e:
            logger.error(f"Exceção ao renovar token para {tenant.company_name}: {e}")
            return False

    async def refresh_all_tokens(self):
        """Verifica e renova tokens de todos os tenants que estão prestes a expirar."""
        logger.info("Verificando tokens de todos os tenants...")
        for tenant in self.get_all_tenants():
            if tenant.is_token_expired:
                await self._refresh_token(tenant)


# Instância global
token_manager = TokenManager()
