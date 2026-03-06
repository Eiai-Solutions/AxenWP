import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from utils.config import settings

# Ajusta a URL para o caso especial do postgres:// antigo que algumas plataformas fornecem
# SQLAlchemy espera postgresql://
SQLALCHEMY_DATABASE_URL = settings.database_url
if SQLALCHEMY_DATABASE_URL.startswith("postgres://"):
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Se for sqlite, precisamos de args específicos
connect_args = {"check_same_thread": False} if SQLALCHEMY_DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args=connect_args
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    """Dependência para injetar o DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
