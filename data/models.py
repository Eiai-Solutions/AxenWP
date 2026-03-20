from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, Boolean, JSON, Integer, ForeignKey, Text, Float
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

    created_at = Column(String, default=lambda: datetime.now(timezone.utc).isoformat())

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
    knowledge_documents = relationship("KnowledgeDocument", back_populates="tenant", cascade="all, delete-orphan")


class AIAgent(Base):
    __tablename__ = "ai_agents"
    
    id = Column(Integer, primary_key=True, index=True)
    location_id = Column(String, ForeignKey("tenants.location_id", ondelete="CASCADE"), unique=True, index=True, nullable=False)
    name = Column(String, nullable=False, default="Agente Inteligente")
    prompt = Column(Text, nullable=False, default="Você é um assistente virtual prestativo.")
    model = Column(String, nullable=False, default="openai/gpt-4o") # Formato OpenRouter
    # OpenRouter
    api_key = Column(String(255), nullable=True)

    # ElevenLabs - Fase 3 Voz
    elevenlabs_api_key = Column(String(255), nullable=True)
    elevenlabs_voice_id = Column(String(100), nullable=True)
    always_reply_with_audio = Column(Boolean, default=False)

    # ElevenLabs - Voice Settings
    elevenlabs_speed = Column(Float, default=1.0, nullable=True)
    elevenlabs_stability = Column(Float, default=0.5, nullable=True)
    elevenlabs_similarity = Column(Float, default=0.75, nullable=True)

    # Groq - Transcrição de áudio (Whisper)
    groq_api_key = Column(String(255), nullable=True)
    
    is_active = Column(Boolean, default=False)
    debounce_seconds = Column(Float, default=1.5, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relações
    tenant = relationship("Tenant", back_populates="ai_agent")

class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String, ForeignKey("tenants.location_id", ondelete="CASCADE"), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    file_type = Column(String(50), nullable=True)  # ex: pdf, txt
    blob_url = Column(String(512), nullable=True)  # Se upado em S3/Supabase Storage
    is_indexed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relações
    tenant = relationship("Tenant", back_populates="knowledge_documents")

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
    created_at = Column(String, default=lambda: datetime.now(timezone.utc).isoformat())


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
    created_at = Column(String, default=lambda: datetime.now(timezone.utc).isoformat())


class SystemSettings(Base):
    """
    Tabela de configuração global do sistema (apenas 1 registro esperado id=1).
    Guarda as credenciais de Admin para a IA analisadora de prompts.
    """
    __tablename__ = "system_settings"

    id = Column(Integer, primary_key=True, index=True)
    admin_openrouter_key = Column(String(512), nullable=True)
    admin_openrouter_model = Column(String(100), default="openai/gpt-4o")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


