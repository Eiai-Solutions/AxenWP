"""
As migrations rodam dentro do lifespan; o fileConfig do alembic não pode
silenciar o logger da aplicação — sem isso, produção fica cega após o startup.
"""

import logging
from logging.config import fileConfig
from pathlib import Path

ALEMBIC_INI = str(Path(__file__).resolve().parents[1] / "alembic.ini")


def test_logger_do_app_continua_ativo_apos_o_fileConfig_do_alembic():
    from utils.logger import logger

    logger.disabled = False
    # Mesma chamada que alembic/env.py faz no startup.
    fileConfig(ALEMBIC_INI, disable_existing_loggers=False)

    assert logging.getLogger("axenwp").disabled is False


def test_o_padrao_do_fileConfig_realmente_silenciaria(monkeypatch):
    """Guarda de regressão: prova que o parâmetro é o que importa aqui."""
    from utils.logger import logger

    logger.disabled = False
    fileConfig(ALEMBIC_INI)  # padrão = disable_existing_loggers=True
    assert logging.getLogger("axenwp").disabled is True

    # Restaura para não contaminar os outros testes.
    fileConfig(ALEMBIC_INI, disable_existing_loggers=False)
    assert logging.getLogger("axenwp").disabled is False
