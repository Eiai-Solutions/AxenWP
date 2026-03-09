from datetime import datetime
from sqlalchemy import Column, String, DateTime, Boolean, JSON, Integer, ForeignKey, Text
from sqlalchemy.orm import relationship

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
    
    # State flags
    is_active = Column(Boolean, default=True)

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

    # Relações
    ai_agent = relationship("AIAgent", back_populates="tenant", uselist=False, cascade="all, delete-orphan")


class AIAgent(Base):
    __tablename__ = "ai_agents"
    
    id = Column(Integer, primary_key=True, index=True)
    location_id = Column(String, ForeignKey("tenants.location_id", ondelete="CASCADE"), unique=True, index=True, nullable=False)
    name = Column(String, nullable=False, default="Agente Inteligente")
    prompt = Column(Text, nullable=False, default="Você é um assistente virtual prestativo.")
    model = Column(String, nullable=False, default="openai/gpt-4o") # Formato OpenRouter
    api_key = Column(String, nullable=True) # OpenRouter API Key
    is_active = Column(Boolean, default=False)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    tenant = relationship("Tenant", back_populates="ai_agent")

class ChatHistory(Base):
    """
    Armazena o histórico do langchain na unha ou pra fallback.
    session_id é o número do cliente (ou ID da conversa).
    """
    __tablename__ = "chat_histories"
    
    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String, index=True, nullable=False) # Ex: numero de telefone +55...
    message_type = Column(String, nullable=False) # "human", "ai", "system"
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class ContactMapping(Base):
    """
    Tabela auxiliar para mapear números bizarros (como o @lid do WhatsApp gerado por anúncios da Meta)
    ou telefones normais para seus respectivos `contact_id` no GoHighLevel.
    Isso evita criar campos customizados no GHL e duplicidade na busca.
    """
    __tablename__ = "contact_mappings"

    id = Column(String, primary_key=True, index=True) # Ex: location_id + phone
    location_id = Column(String, index=True)
    phone_or_lid = Column(String, index=True) # O identificador que a Z-API nos manda (ex: 5511999999999 ou 12345678@lid)
    ghl_contact_id = Column(String, index=True) # O ID real do contato no GHL
    created_at = Column(String, default=lambda: datetime.utcnow().isoformat())


class MessageMapping(Base):
    """
    Tabela para mapear o messageId do GHL com o zapiMessageId.
    Isso é necessário para atualizar o status (entregue, lido, falhou) no GHL
    quando recebemos o webhook de status da Z-API.
    """
    __tablename__ = "message_mappings"

    zapi_message_id = Column(String, primary_key=True, index=True) # O ID gerado pela Z-API
    ghl_message_id = Column(String, index=True) # O ID original do GHL (payload.messageId)
    location_id = Column(String, index=True)
    status = Column(String, default="pending")
    created_at = Column(String, default=lambda: datetime.utcnow().isoformat())

