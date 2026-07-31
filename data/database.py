import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from utils.config import settings

# Ajusta a URL para o caso especial do postgres:// antigo que algumas plataformas fornecem
# SQLAlchemy espera postgresql://
SQLALCHEMY_DATABASE_URL = (settings.database_url or "").strip()

# DATABASE_URL="" (definida porém vazia) zera o default do Pydantic e o erro que
# sobra é um ArgumentError opaco do SQLAlchemy lá na frente. Falha aqui, claro.
if not SQLALCHEMY_DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL está definida porém vazia. Aponte para o banco "
        "(postgresql://...) ou remova a variável para cair no default."
    )

if SQLALCHEMY_DATABASE_URL.startswith("postgres://"):
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.replace("postgres://", "postgresql://", 1)

_e_sqlite = SQLALCHEMY_DATABASE_URL.startswith("sqlite")

# Alçapão: settings.database_url tem DEFAULT de SQLite. Se a env var sumir em
# produção (removida, ou o nome digitado errado), o app não quebra — ele sobe
# num SQLite vazio, /health responde "healthy" e todo tenant simplesmente
# desaparece, sem um único erro no log. Só é fallback legítimo quando alguém
# pediu SQLite de propósito (testes/dev exportam DATABASE_URL) ou em DEBUG.
if _e_sqlite and not os.environ.get("DATABASE_URL") and not settings.debug:
    raise RuntimeError(
        "DATABASE_URL não está definida e o default é SQLite. Em produção isso "
        "subiria um banco vazio reportando 'healthy'. Defina DATABASE_URL "
        "(postgresql://...) ou ligue DEBUG para usar SQLite local."
    )

if _e_sqlite:
    connect_args = {"check_same_thread": False}
    engine_kwargs = {}
else:
    # Banco REMOTO (Supabase): a conexão atravessa a rede e o pooler derruba
    # conexão ociosa. Sem pool_pre_ping ela volta morta do pool e o próximo
    # checkout estoura OperationalError — que vira 500 no webhook e mensagem de
    # lead perdida, porque não existe retry em nenhum ponto do projeto.
    # Contra um Postgres local isso nunca aparecia: a conexão nunca morria só.
    connect_args = {
        "connect_timeout": 10,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 3,
        "application_name": "millochat",
    }
    # Respeita o sslmode da URL se ela já disser; senão exige TLS.
    if "sslmode" not in SQLALCHEMY_DATABASE_URL:
        connect_args["sslmode"] = "require"

    engine_kwargs = {
        "pool_pre_ping": True,   # valida a conexão antes de entregar
        "pool_recycle": 300,     # descarta antes do pooler derrubar
        "pool_size": 5,
        "max_overflow": 5,
        "pool_timeout": 10,
    }

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args=connect_args, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    """Dependência para injetar o DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
