"""
Logging configurado para o Axen WP.
Formato padronizado com timestamp, nível e contexto do módulo.
"""

import logging
import sys
import os


def setup_logger(name: str = "axenwp") -> logging.Logger:
    """Cria e retorna um logger configurado."""
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, log_level, logging.INFO))

    # Evita handlers duplicados
    if logger.handlers:
        return logger

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(getattr(logging, log_level, logging.INFO))

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    # Sem propagar para o root: o alembic configura o root no startup (fileConfig
    # do alembic.ini) e, propagando, cada linha sairia duas vezes em produção —
    # uma no nosso formato e outra no do alembic.
    logger.propagate = False

    return logger


# Logger global da aplicação
logger = setup_logger()
