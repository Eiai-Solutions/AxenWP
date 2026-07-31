"""
As migrations rodam dentro do lifespan; o fileConfig do alembic não pode
silenciar o logger da aplicação — sem isso, produção fica cega após o startup.
"""

import logging
import pytest
from logging.config import fileConfig
from pathlib import Path

ALEMBIC_INI = str(Path(__file__).resolve().parents[1] / "alembic.ini")


def test_logger_do_app_continua_ativo_apos_o_fileConfig_do_alembic():
    from utils.logger import logger

    logger.disabled = False
    # Mesma chamada que alembic/env.py faz no startup.
    fileConfig(ALEMBIC_INI, disable_existing_loggers=False)

    assert logging.getLogger("millochat").disabled is False


def test_o_padrao_do_fileConfig_realmente_silenciaria(monkeypatch):
    """Guarda de regressão: prova que o parâmetro é o que importa aqui."""
    from utils.logger import logger

    logger.disabled = False
    fileConfig(ALEMBIC_INI)  # padrão = disable_existing_loggers=True
    assert logging.getLogger("millochat").disabled is True

    # Restaura para não contaminar os outros testes.
    fileConfig(ALEMBIC_INI, disable_existing_loggers=False)
    assert logging.getLogger("millochat").disabled is False


def test_migration_que_falha_derruba_o_boot(monkeypatch):
    """
    Engolir a exceção não evitava a queda — só escondia a causa.

    Aconteceu em produção em 2026-07-31: a migration 030 falhou com
    "must be owner of table tenants", o erro virou uma linha de log, o app subiu
    com schema parcial e morreu na primeira query com "column organization_id
    does not exist" — apontando para o sintoma, dezenas de linhas depois da causa.
    """
    import asyncio

    import main

    def upgrade_que_falha(*a, **kw):
        raise RuntimeError("must be owner of table tenants")

    monkeypatch.setattr("alembic.command.upgrade", upgrade_que_falha, raising=True)
    monkeypatch.setattr(main.Base.metadata, "create_all", lambda **kw: None, raising=False)

    async def sobe():
        async with main.lifespan(main.app):
            pass

    with pytest.raises(RuntimeError, match="must be owner"):
        asyncio.run(sobe())
