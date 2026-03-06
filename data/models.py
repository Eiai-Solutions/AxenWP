from datetime import datetime
from sqlalchemy import Column, String, DateTime

from data.database import Base

class Tenant(Base):
    __tablename__ = "tenants"

    location_id = Column(String, primary_key=True, index=True)
    company_name = Column(String, index=True)
    client_id = Column(String)
    client_secret = Column(String)
    access_token = Column(String)
    refresh_token = Column(String)
    token_expires_at = Column(String)
    
    # Z-API configs
    zapi_instance_id = Column(String, nullable=True)
    zapi_token = Column(String, nullable=True)
    zapi_client_token = Column(String, nullable=True)
    
    # GHL App Configs
    conversation_provider_id = Column(String, nullable=True)
    
    created_at = Column(String, default=lambda: datetime.utcnow().isoformat())

    @property
    def is_token_expired(self) -> bool:
        """Verifica se o access_token está expirado ou prestes a expirar (margem de 1h)."""
        if not self.token_expires_at:
            return True
        from datetime import timezone, timedelta
        try:
            expires = datetime.fromisoformat(self.token_expires_at.replace("Z", "+00:00"))
            margin = timedelta(hours=1)
            return datetime.now(timezone.utc) >= (expires - margin)
        except (ValueError, TypeError):
            return True
