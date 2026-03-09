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

from data.database import Base, engine

# =============================================================================
# Ciclo de Vida do FastAPI (Start/Shutdown)
# =============================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Inicializa banco de dados
    Base.metadata.create_all(bind=engine)
    logger.info("Tabelas do banco de dados verificadas/criadas.")

    # Automigração simples para adicionar colunas faltantes no PostgreSQL/SQLite
    from sqlalchemy import text
    
    col_exists = True
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT is_active FROM tenants LIMIT 1"))
    except Exception:
        col_exists = False

    if not col_exists:
        try:
            logger.info("Coluna 'is_active' não encontrada. Adicionando na tabela tenants...")
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE tenants ADD COLUMN is_active BOOLEAN DEFAULT true"))
        except Exception as e:
            logger.error(f"Erro ao adicionar coluna: {e}")

    # Inicializa scheduler de token refresh a cada 12 horas (proteção)
    # E roda imediatamente na subida
    logger.info("Axen WP Server iniciando...")
    scheduler.add_job(refresh_tokens_job, "interval", hours=12)
    scheduler.start()
    
    await refresh_tokens_job()
    
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
    """Retorna estado do servidor e os tenants ativos."""
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
        "tenants_loaded": len(tenants),
        "tenants": active_tenants
    }


if __name__ == "__main__":
    logger.info(f"Starting uvicorn server on {settings.host}:{settings.port}...")
    uvicorn.run("main:app", host=settings.host, port=settings.port, reload=True)
