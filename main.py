"""
Ponto de entrada do servidor FastAPI do Axen WP.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from utils.logger import logger
from utils.config import settings
from auth.token_manager import token_manager

# Importa as rotas
from auth.oauth import router as oauth_router
from webhooks.ghl_provider import router as ghl_webhook_router
from webhooks.zapi_receiver import router as zapi_webhook_router
from admin.dashboard import router as admin_router
from admin.ai_agent import router as admin_ai_agent_router

# =============================================================================
# Configuração do APScheduler (Tokens)
# =============================================================================
scheduler = AsyncIOScheduler()

async def refresh_tokens_job():
    logger.info("Executando job periódico de refresh de tokens...")
    await token_manager.refresh_all_tokens()

from data.database import Base, engine, SessionLocal
from data.models import ChatHistory

# =============================================================================
# Limpeza periódica de histórico antigo
# =============================================================================
def cleanup_old_chat_history(days: int = 30):
    """Remove entradas de chat_histories com mais de `days` dias."""
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    db = SessionLocal()
    try:
        deleted = db.query(ChatHistory).filter(ChatHistory.created_at < cutoff).delete()
        db.commit()
        if deleted:
            logger.info(f"Limpeza de histórico: {deleted} mensagens antigas removidas (>{days} dias).")
        else:
            logger.debug("Limpeza de histórico: nenhuma mensagem antiga encontrada.")
    except Exception as e:
        logger.error(f"Erro na limpeza de histórico: {e}")
        db.rollback()
    finally:
        db.close()


# =============================================================================
# Ciclo de Vida do FastAPI (Start/Shutdown)
# =============================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Cria tabelas novas que ainda não existem no banco (idempotente)
    Base.metadata.create_all(bind=engine)
    logger.info("Tabelas do banco de dados verificadas/criadas.")

    # 2. Aplica migrações de schema via Alembic (adiciona colunas, etc.)
    import os
    from alembic.config import Config as AlembicConfig
    from alembic import command as alembic_command
    try:
        alembic_cfg = AlembicConfig(os.path.join(os.path.dirname(__file__), "alembic.ini"))
        alembic_command.upgrade(alembic_cfg, "head")
        logger.info("Migrações Alembic aplicadas com sucesso.")
    except Exception as e:
        logger.error(f"Erro ao aplicar migrações Alembic: {e}", exc_info=True)

    # Inicializa scheduler de token refresh a cada 12 horas (proteção)
    # E roda imediatamente na subida
    logger.info("Axen WP Server iniciando...")
    scheduler.add_job(refresh_tokens_job, "interval", hours=12)
    scheduler.add_job(cleanup_old_chat_history, "interval", hours=24)
    scheduler.start()
    
    await refresh_tokens_job()
    cleanup_old_chat_history()
    
    yield
    
    logger.info("Desligando servidor...")
    scheduler.shutdown()

# =============================================================================
# App FastAPI
# =============================================================================
app = FastAPI(
    title="Axen WP - WhatsApp Automation",
    description="Hub de integração GHL Custom Conversation Provider e Z-API",
    version="1.0.0",
    lifespan=lifespan
)

# CORS: restrito a origens configuradas; em debug mode permite tudo
_cors_origins = (
    [o.strip() for o in settings.allowed_origins.split(",") if o.strip()]
    if settings.allowed_origins
    else []
)
if not _cors_origins and not settings.debug:
    logger.warning(
        "ALLOWED_ORIGINS nao configurado e DEBUG=false. "
        "CORS bloqueara requests cross-origin. "
        "Configure ALLOWED_ORIGINS no .env."
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins if _cors_origins else (["*"] if settings.debug else []),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Registro de Rotas
app.include_router(oauth_router)
app.include_router(ghl_webhook_router)
app.include_router(zapi_webhook_router)
app.include_router(admin_router)
app.include_router(admin_ai_agent_router)


# Montagem de arquivos estáticos
app.mount("/static", StaticFiles(directory="web/static"), name="static")

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse("web/static/favicon.svg", media_type="image/svg+xml")

@app.get("/", tags=["Health"])
async def root():
    # Redireciona a raiz para o admin se acessada no navegador
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/admin/dashboard", status_code=303)


@app.get("/health", tags=["Health"])
async def health_check():
    """Retorna estado do servidor, conectividade do DB e os tenants ativos."""
    import asyncio
    from sqlalchemy import text

    # Verify database connectivity
    db_ok = False
    try:
        def _check_db():
            db = SessionLocal()
            try:
                db.execute(text("SELECT 1"))
                return True
            finally:
                db.close()
        db_ok = await asyncio.to_thread(_check_db)
    except Exception as e:
        logger.error(f"Health check: DB unreachable — {e}")

    if not db_ok:
        return {"status": "unhealthy", "database": "unreachable"}

    tenants = token_manager.get_all_tenants()
    active_tenants = []

    for t in tenants:
        active_tenants.append({
            "company": t.company_name,
            "location_id": t.location_id,
            "token_valid": not t.is_token_expired,
            "zapi_configured": bool(t.zapi_instance_id and t.zapi_token),
            "zapi_instance_id": t.zapi_instance_id
        })

    return {
        "status": "healthy",
        "database": "connected",
        "tenants_loaded": len(tenants),
        "tenants": active_tenants
    }


if __name__ == "__main__":
    logger.info(f"Starting uvicorn server on {settings.host}:{settings.port}...")
    uvicorn.run("main:app", host=settings.host, port=settings.port, reload=settings.debug)
