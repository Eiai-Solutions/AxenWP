"""
Configurações globais carregadas do .env.
"""

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Configurações da aplicação. Valores lidos do arquivo .env."""

    # GoHighLevel App
    ghl_client_id: str = Field(default="", description="Client ID do app no Marketplace")
    ghl_client_secret: str = Field(default="", description="Client Secret do app")
    ghl_redirect_uri: str = Field(
        default="http://localhost:8000/oauth/callback",
        description="URI de callback OAuth",
    )
    ghl_conversation_provider_id: str = Field(
        default="", description="ID do Conversation Provider Axen WP"
    )

    database_url: str = Field(
        default="sqlite:///./data/axenwp.db",
        description="URL de conexão com o banco de dados (ex: postgresql://...)"
    )

    # Servidor
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)

    # Segurança
    zapi_webhook_secret: str = Field(
        default="", description="Token para validar webhooks do Z-API"
    )

    # Logs
    log_level: str = Field(default="INFO")

    # GHL API Base
    ghl_api_base: str = Field(
        default="https://services.leadconnectorhq.com",
        description="Base URL da API GHL",
    )

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


# Instância global
settings = Settings()
