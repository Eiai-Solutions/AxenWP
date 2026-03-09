"""
Gerenciamento de tokens OAuth do GoHighLevel usando PostgreSQL (SQLAlchemy).
Renova tokens automaticamente quando expiram.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from utils.logger import logger
from utils.config import settings
from data.database import SessionLocal
from data.models import Tenant

class TokenManager:
    """Gerencia tokens de todos os tenants armazenados no bando de dados."""

    def __init__(self):
        pass

    def get_tenant(self, location_id: str, db: Session = None) -> Optional[Tenant]:
        """Retorna o tenant pelo location_id a partir do banco de dados."""
        session = db or SessionLocal()
        try:
            return session.query(Tenant).filter(Tenant.location_id == location_id).first()
        finally:
            if not db:
                session.close()

    def get_all_tenants(self, db: Session = None) -> list[Tenant]:
        """Retorna todos os tenants cadastrados no banco."""
        session = db or SessionLocal()
        try:
            return session.query(Tenant).all()
        finally:
            if not db:
                session.close()

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
    ) -> Tenant:
        """Registra (ou atualiza) um tenant com os dados do OAuth no DB."""
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

        db = SessionLocal()
        try:
            tenant = self.get_tenant(location_id, db=db)
            if not tenant:
                tenant = Tenant(location_id=location_id)
                db.add(tenant)

            tenant.company_name = company_name or tenant.company_name
            tenant.client_id = client_id or tenant.client_id or settings.ghl_client_id
            tenant.client_secret = client_secret or tenant.client_secret or settings.ghl_client_secret
            tenant.access_token = access_token
            tenant.refresh_token = refresh_token
            tenant.token_expires_at = expires_at.isoformat()
            
            # Atualiza extras se existirem
            for key, value in extras.items():
                if hasattr(tenant, key):
                    setattr(tenant, key, value)

            db.commit()
            db.refresh(tenant)
            
            logger.info(f"Tenant {tenant.company_name} ({tenant.location_id}) registrado no banco")
            return tenant
        finally:
            db.close()
            
    def update_zapi_credentials(self, location_id: str, instance_id: str, token: str, client_token: str = ""):
        """Atualiza as credenciais Z-API de um determinado tenant."""
        db = SessionLocal()
        try:
            tenant = self.get_tenant(location_id, db=db)
            if tenant:
                tenant.zapi_instance_id = instance_id
                tenant.zapi_token = token
                tenant.zapi_client_token = client_token
                db.commit()
                logger.info(f"Credenciais Z-API salvas no banco para {tenant.company_name}")
        finally:
            db.close()

    def toggle_active_status(self, location_id: str, is_active: bool):
        """Ativa/desativa a automação inteira no nível do Tenant."""
        db = SessionLocal()
        try:
            tenant = self.get_tenant(location_id, db=db)
            if tenant:
                tenant.is_active = is_active
                db.commit()
                logger.info(f"Automação de {tenant.company_name} alterada para {'ATIVA' if is_active else 'PAUSADA'}")
        finally:
            db.close()

    def is_token_expired(self, tenant: Tenant) -> bool:
        """Verifica se o access_token está expirado ou prestes a expirar."""
        if not tenant.token_expires_at:
            return True
        try:
            expires = datetime.fromisoformat(tenant.token_expires_at.replace("Z", "+00:00"))
            margin = timedelta(hours=1)
            return datetime.now(timezone.utc) >= (expires - margin)
        except (ValueError, TypeError):
            return True

    async def get_valid_token(self, location_id: str) -> Optional[str]:
        """
        Retorna um access_token válido.
        Se estiver expirado, faz o refresh automaticamente.
        """
        tenant = self.get_tenant(location_id)
        if not tenant:
            logger.error(f"Tenant {location_id} não encontrado")
            return None

        if not self.is_token_expired(tenant):
            return tenant.access_token

        # Precisa renovar
        logger.info(f"Token expirado para {tenant.company_name}, renovando...")
        success = await self._refresh_token(tenant.location_id)
        if success:
            return self.get_tenant(location_id).access_token

        logger.error(f"Falha ao renovar token para {tenant.company_name}")
        return None

    async def _refresh_token(self, location_id: str) -> bool:
        """Faz o refresh do access_token via API GHL."""
        db = SessionLocal()
        try:
            tenant = self.get_tenant(location_id, db=db)
            if not tenant:
                return False

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

                db.commit()

                logger.info(f"Token renovado com sucesso para {tenant.company_name}")
                return True

        except Exception as e:
            logger.error(f"Exceção ao renovar token: {e}")
            return False
        finally:
            db.close()

    async def refresh_all_tokens(self):
        """Verifica e renova tokens de todos os tenants que estão prestes a expirar."""
        logger.info("Verificando tokens de todos os tenants no banco...")
        db = SessionLocal()
        try:
            tenants = db.query(Tenant).all()
            for tenant in tenants:
                if self.is_token_expired(tenant):
                    await self._refresh_token(tenant.location_id)
        finally:
            db.close()

    # --- CONTACT MAPPING FUNCS ---
    def get_mapped_contact_id(self, location_id: str, phone_or_lid: str) -> Optional[str]:
        """Tenta achar um ID de contato do GHL associado a um telefone_ou_lid no banco local."""
        from data.models import ContactMapping
        db = SessionLocal()
        try:
            mapping = db.query(ContactMapping).filter_by(
                location_id=location_id, 
                phone_or_lid=phone_or_lid
            ).first()
            return mapping.ghl_contact_id if mapping else None
        finally:
            db.close()

    def save_contact_mapping(self, location_id: str, phone_or_lid: str, ghl_contact_id: str):
        """Salva a associação entre o numero do WhatsApp (@lid) e o ID real do GHL."""
        from data.models import ContactMapping
        db = SessionLocal()
        try:
            mapping_id = f"{location_id}_{phone_or_lid}"
            mapping = db.query(ContactMapping).filter_by(id=mapping_id).first()
            if not mapping:
                mapping = ContactMapping(
                    id=mapping_id,
                    location_id=location_id,
                    phone_or_lid=phone_or_lid,
                    ghl_contact_id=ghl_contact_id
                )
                db.add(mapping)
            else:
                mapping.ghl_contact_id = ghl_contact_id
            db.commit()
        except Exception as e:
            logger.error(f"Erro ao salvar mapping de contato DB: {e}")
        finally:
            db.close()

    def delete_contact_mapping(self, location_id: str, phone_or_lid: str):
        """Remove o mapeamento em caso de contato deletado no GHL."""
        from data.models import ContactMapping
        db = SessionLocal()
        try:
            mapping_id = f"{location_id}_{phone_or_lid}"
            db.query(ContactMapping).filter_by(id=mapping_id).delete()
            db.commit()
        except Exception as e:
            logger.error(f"Erro ao deletar mapping de contato DB: {e}")
        finally:
            db.close()

    # --- MESSAGE MAPPING FUNCS ---
    def save_message_mapping(self, zapi_message_id: str, ghl_message_id: str, location_id: str):
        """Salva a associação entre o ID da mensagem na Z-API e no GHL."""
        from data.models import MessageMapping
        db = SessionLocal()
        try:
            mapping = db.query(MessageMapping).filter_by(zapi_message_id=zapi_message_id).first()
            if not mapping:
                mapping = MessageMapping(
                    zapi_message_id=zapi_message_id,
                    ghl_message_id=ghl_message_id,
                    location_id=location_id
                )
                db.add(mapping)
            else:
                mapping.ghl_message_id = ghl_message_id
            db.commit()
        except Exception as e:
            logger.error(f"Erro ao salvar mapping de mensagem DB: {e}")
        finally:
            db.close()

    def get_ghl_message_id_by_zapi(self, zapi_message_id: str) -> Optional[dict]:
        """Tenta achar o ID da mensagem no GHL pelo ID da Z-API."""
        from data.models import MessageMapping
        db = SessionLocal()
        try:
            mapping = db.query(MessageMapping).filter_by(zapi_message_id=zapi_message_id).first()
            if mapping:
                return {
                    "ghl_message_id": mapping.ghl_message_id,
                    "location_id": mapping.location_id
                }
            return None
        finally:
            db.close()

# Instância global
token_manager = TokenManager()
