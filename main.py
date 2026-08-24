"""
Ponto de entrada do servidor FastAPI do MilloChat.
"""

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from utils.limiter import limiter

from utils.logger import logger
from utils.config import settings
from auth.token_manager import token_manager
from services.ghl_service import ghl_service
from services.zapi_service import zapi_service
from services.telegram_service import telegram_service
from services.waha_service import waha_service

# Importa as rotas
from auth.oauth import router as oauth_router
from webhooks.ghl_provider import router as ghl_webhook_router
from webhooks.zapi_receiver import router as zapi_webhook_router
from webhooks.telegram_receiver import router as telegram_webhook_router
from webhooks.waha_receiver import router as waha_webhook_router
from admin.dashboard import require_admin
from admin.dashboard import router as admin_router
from admin.dashboard import router_publico as admin_router_publico
from admin.ai_agent import router as admin_ai_agent_router
from admin.seed_joorney import router as seed_joorney_router
from admin.diagnostics import router as diagnostics_router
from admin.inspect import router as inspect_router
from admin.waha import router as waha_router
from admin.channels import router as channels_router
from webhooks.media_proxy import router as media_router
from public.onboarding import router as onboarding_router

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
        # FATAL, e não engolido. Engolir não evitava a queda: o app subia com
        # schema parcial e morria na primeira query, com um erro apontando para a
        # coluna faltante em vez da migration que falhou — a causa ficava a dezenas
        # de linhas de distância no log. Falhar aqui dá a mesma indisponibilidade
        # com a causa na primeira linha.
        logger.critical(
            f"MIGRATION FALHOU — o app NÃO vai subir para não operar com schema "
            f"parcial. Causa: {type(e).__name__}: {e}",
            exc_info=True,
        )
        raise

    # 3. Garante que existe pelo menos um operador do painel.
    # FORA do try do Alembic de propósito: se a migration falhar, a exceção acima é
    # engolida e o app sobe assim mesmo — se o bootstrap estivesse lá dentro, ele
    # seria pulado e ninguém conseguiria entrar no painel. A tabela `admin_users`
    # já foi garantida pelo `create_all` do passo 1, que não depende do Alembic.
    try:
        from services.admin_auth import bootstrap_admin_user
        bootstrap_admin_user()
    except Exception as e:
        logger.error(f"Falha no bootstrap do operador admin: {e}", exc_info=True)

    # Inicializa scheduler de token refresh a cada 12 horas (proteção)
    # E roda imediatamente na subida
    logger.info("MilloChat Server iniciando...")
    from webhooks.zapi_receiver import cleanup_stale_debounce_entries
    from webhooks.telegram_receiver import cleanup_stale_telegram_debounce
    from services.inbound_pipeline import cleanup_stale_entries as cleanup_pipeline_entries
    from services.media_store import cleanup_old_media
    scheduler.add_job(refresh_tokens_job, "interval", hours=12)
    scheduler.add_job(cleanup_old_chat_history, "interval", hours=24)
    scheduler.add_job(cleanup_old_media, "interval", hours=24)
    scheduler.add_job(cleanup_stale_debounce_entries, "interval", minutes=10)
    scheduler.add_job(cleanup_stale_telegram_debounce, "interval", minutes=10)
    scheduler.add_job(cleanup_pipeline_entries, "interval", minutes=10)
    scheduler.start()
    
    # Inicializa clientes HTTP compartilhados
    await ghl_service.startup()
    await zapi_service.startup()
    await telegram_service.startup()
    await waha_service.startup()

    await refresh_tokens_job()
    cleanup_old_chat_history()

    yield

    logger.info("Desligando servidor...")
    await ghl_service.shutdown()
    await zapi_service.shutdown()
    await telegram_service.shutdown()
    await waha_service.shutdown()
    scheduler.shutdown()

# =============================================================================
# App FastAPI
# =============================================================================
app = FastAPI(
    title="MilloChat - WhatsApp Automation",
    description="Hub de integração GHL Custom Conversation Provider e Z-API",
    version="1.0.0",
    lifespan=lifespan,
    # `/docs`, `/redoc` e `/openapi.json` só em DEBUG.
    #
    # Em produção eles entregavam, sem autenticação, o mapa completo do hub: as
    # ~90 rotas de `/admin`, a API de tenant, e o formato exato de cada webhook de
    # entrada. Não vaza credencial nem location_id, mas poupa ao atacante todo o
    # trabalho de descobrir onde bater — e o resto da defesa depende de segredo,
    # não de obscuridade, justamente porque o mapa nunca é garantido. Ainda assim,
    # publicá-lo de graça é dar o primeiro passo do trabalho dele.
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
    openapi_url="/openapi.json" if settings.debug else None,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

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
app.include_router(telegram_webhook_router)
app.include_router(waha_webhook_router)
app.include_router(admin_router_publico)  # login/logout — sem barreira, de propósito
app.include_router(admin_router)
app.include_router(admin_ai_agent_router)
app.include_router(seed_joorney_router)
app.include_router(diagnostics_router)
app.include_router(inspect_router)
app.include_router(waha_router)
app.include_router(channels_router)
app.include_router(media_router)
app.include_router(onboarding_router)
# API pública do hub: autenticada por CHAVE DE TENANT, não pelo cookie do painel.
# É por aqui que um CRM de terceiro comanda a IA.
from api.v1 import router as api_v1_router  # noqa: E402
app.include_router(api_v1_router)


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
    """
    Sinal de vida para o orquestrador. **Público — e por isso anônimo.**

    Até 2026-08-20 esta rota devolvia, sem autenticação nenhuma, a lista de
    tenants com `company_name` e `location_id`. E o `location_id` não é um id
    qualquer: ele é o caminho do webhook de entrada
    (`/webhook/waha/{location_id}`, `/webhook/zapi/inbound/{location_id}`,
    `/webhook/telegram/{location_id}`). Quem varre a internet ganhava o mapa
    completo de graça, e a única coisa entre o mapa e injetar mensagem na IA de um
    cliente é a assinatura do webhook — que hoje é opt-in e está desligada.

    O healthcheck do EasyPanel/Traefik só olha o código HTTP. Quem precisa da
    lista é o operador, e ele tem `GET /admin/health` atrás do cookie.
    """
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

    # Contagens agregadas ficam: são números, não identificadores. O que saiu foi
    # a LISTA — nome da empresa e location_id, que é o caminho do webhook dela.
    from data.models import AIAgent, QualifiedLead
    db = SessionLocal()
    try:
        agents_active = db.query(AIAgent).filter(AIAgent.is_active.is_(True)).count()
        agents_total = db.query(AIAgent).count()
        qualified_total = db.query(QualifiedLead).count()
    except Exception as e:
        logger.error(f"Health: erro consultando contagens: {e}")
        agents_active = agents_total = qualified_total = -1
    finally:
        db.close()

    return {
        "status": "healthy",
        "database": "connected",
        "tenants_loaded": len(token_manager.get_all_tenants()),
        "agents_active": agents_active,
        "agents_total": agents_total,
        "qualified_leads_total": qualified_total,
    }


@app.get("/admin/health", tags=["Health"], dependencies=[Depends(require_admin)])
async def health_detalhado():
    """O que o /health devolvia — agora atrás do cookie do operador."""
    from services import webhook_auth
    from utils.metrics import snapshot as metrics_snapshot

    return {
        # Sem isto, "a assinatura dos webhooks está valendo?" só se responde lendo
        # env var no servidor — e é exatamente a pergunta que decide se dá para
        # virar o modo para `enforce` sem perder mensagem de lead.
        "webhook_auth": webhook_auth.estado_para_o_painel(),
        "tenants": [{
            "company": t.company_name,
            "location_id": t.location_id,
            "token_valid": not t.is_token_expired,
            "zapi_configured": bool(t.zapi_instance_id and t.zapi_token),
            "zapi_instance_id": t.zapi_instance_id,
        } for t in token_manager.get_all_tenants()],
        "metrics_summary": metrics_snapshot(),
    }


@app.get("/metrics", tags=["Health"], include_in_schema=False)
async def metrics_endpoint():
    """
    Expose counters in Prometheus exposition format.
    Em deploy multi-worker, números são parciais por worker (process-local).
    """
    from fastapi.responses import PlainTextResponse
    from utils.metrics import prometheus_text
    return PlainTextResponse(content=prometheus_text(), media_type="text/plain; version=0.0.4")


if __name__ == "__main__":
    logger.info(f"Starting uvicorn server on {settings.host}:{settings.port}...")
    uvicorn.run("main:app", host=settings.host, port=settings.port, reload=settings.debug)
