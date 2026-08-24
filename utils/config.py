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
        default="", description="ID do Conversation Provider MilloChat"
    )

    admin_user: str = Field(
        default="admin",
        description="Nome de usuário do operador inicial do painel (bootstrap)"
    )

    admin_force_reset: bool = Field(
        default=False,
        description=(
            "Resgate: no próximo boot, redefine a senha do ADMIN_USER com o "
            "ADMIN_PASSWORD mesmo que a conta já exista e esteja ativa. "
            "Desligue depois de entrar."
        ),
    )

    database_url: str = Field(
        default="sqlite:///./data/millochat.db",
        description="URL de conexão com o banco de dados (ex: postgresql://...)"
    )

    # Servidor
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)

    # Segurança
    admin_password: str = Field(
        default="", description="Senha do painel admin. OBRIGATORIA em producao."
    )
    zapi_webhook_secret: str = Field(
        default="",
        description=(
            "Segredo dos webhooks da Z-API. Vai no header `x-chat-secret` ou no fim "
            "do caminho. Ficou DECLARADO E NUNCA LIDO até 2026-08-20 — quem o "
            "configurou achou que estava protegido e não estava."
        ),
    )
    waha_webhook_hmac_key: str = Field(
        default="",
        description=(
            "Chave HMAC dos webhooks do WAHA. Vazio = sem assinatura (as sessões já "
            "criadas foram registradas assim). Ao definir, re-registre o webhook das "
            "sessões existentes — e deixe WEBHOOK_AUTH_MODE em 'observe' até a "
            "métrica mostrar que o provedor está mesmo assinando."
        ),
    )
    telegram_webhook_secret: str = Field(
        default="",
        description=(
            "Segredo do webhook do Telegram. Mecanismo nativo: vai no `setWebhook` "
            "e volta em `X-Telegram-Bot-Api-Secret-Token`. Ao definir, re-registre "
            "o webhook do bot."
        ),
    )
    webhook_auth_mode: str = Field(
        default="observe",
        description=(
            "off | observe | enforce. `observe` (default) verifica, conta a métrica "
            "e ACEITA mesmo assim — é o que deixa configurar o segredo sem derrubar "
            "atendimento. Vire para `enforce` só quando "
            "`millochat_webhook_assinatura_total{resultado=\"falhou\"}` parar de subir. "
            "Canal sem segredo fica em `off` de qualquer jeito."
        ),
    )
    allowed_origins: str = Field(
        default="",
        description="Origens permitidas para CORS (separadas por virgula). Ex: https://app.example.com,https://admin.example.com"
    )
    debug: bool = Field(default=False, description="Modo debug (habilita reload, CORS permissivo, etc.)")

    # Logs
    log_level: str = Field(default="INFO")

    # GHL API Base
    ghl_api_base: str = Field(
        default="https://services.leadconnectorhq.com",
        description="Base URL da API GHL",
    )

    # URL pública do servidor (usada para registrar webhooks externos como Telegram).
    # Em produção atrás de proxy, defina explicitamente. Ex: https://millochat.com
    public_base_url: str = Field(default="", description="URL HTTPS pública do servidor")

    # Token de inspeção read-only — quando setado, libera /admin/inspect/* via header
    # X-Inspect-Token. Permite analisar agentes/prompts sem cookie de admin.
    # Gerar com: openssl rand -hex 32
    inspect_token: str = Field(default="", description="Token read-only para /admin/inspect")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


# Instância global
settings = Settings()
