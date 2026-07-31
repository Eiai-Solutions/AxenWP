"""
Garantias da camada de conexão ao migrar para um Postgres REMOTO (Supabase).

Dois riscos que não existiam com o banco local no mesmo host:

1. O default de `settings.database_url` é SQLite. Se DATABASE_URL sumir em
   produção, o app subia num banco vazio e o /health respondia "healthy" —
   falha silenciosa, com todos os tenants desaparecendo sem erro no log.
2. `create_engine` não tinha pool_pre_ping nem pool_recycle. O pooler gerenciado
   derruba conexão ociosa; ela volta morta do pool e estoura OperationalError.
   Como não há retry em lugar nenhum do projeto, isso vira 500 no webhook e
   mensagem de lead perdida.

Cada caso roda em subprocesso: o código sob teste é de nível de módulo, e
recarregá-lo no processo do pytest trocaria o engine que os outros testes usam.
"""

import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]


def _roda(script: str, env_extra: dict) -> subprocess.CompletedProcess:
    """env_extra com valor None REMOVE a variável (≠ de defini-la vazia)."""
    import os

    env = {**os.environ}
    for k, v in env_extra.items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v
    env["PYTHONPATH"] = str(RAIZ)
    return subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, cwd=RAIZ, env=env
    )


def test_sem_database_url_e_sem_debug_o_app_recusa_subir():
    """O alçapão: variável AUSENTE, o default SQLite não pode passar batido."""
    r = _roda(
        "import data.database",
        {"DATABASE_URL": None, "DEBUG": "false", "ADMIN_PASSWORD": "x"},
    )
    assert r.returncode != 0, "deveria ter falhado, mas subiu no SQLite silenciosamente"
    assert "DATABASE_URL não está definida" in (r.stderr or "")


def test_database_url_vazia_falha_com_mensagem_legivel():
    """Definida porém vazia: sem esta guarda, sai um ArgumentError opaco."""
    r = _roda(
        "import data.database",
        {"DATABASE_URL": "", "DEBUG": "false", "ADMIN_PASSWORD": "x"},
    )
    assert r.returncode != 0
    assert "definida porém vazia" in (r.stderr or "")


def test_sqlite_explicito_continua_valendo_para_testes_e_dev():
    """Quem pede SQLite de propósito (exportando a env var) não é bloqueado."""
    r = _roda(
        "import data.database; print('ok')",
        {"DATABASE_URL": "sqlite:///./test.db", "DEBUG": "false", "ADMIN_PASSWORD": "x"},
    )
    assert r.returncode == 0, r.stderr
    assert "ok" in r.stdout


def test_postgres_recebe_pre_ping_e_recycle():
    """Sem isso, conexão reciclada pelo pooler derruba webhook."""
    script = (
        "import data.database as d;"
        "print('PRE_PING=', d.engine.pool._pre_ping);"
        "print('RECYCLE=', d.engine.pool._recycle)"
    )
    r = _roda(
        script,
        {
            "DATABASE_URL": "postgresql://u:p@db.exemplo.supabase.co:5432/postgres?sslmode=require",
            "DEBUG": "false",
            "ADMIN_PASSWORD": "x",
        },
    )
    assert r.returncode == 0, r.stderr
    assert "PRE_PING= True" in r.stdout
    assert "RECYCLE= 300" in r.stdout


def test_tls_exigido_quando_a_url_nao_diz_nada():
    script = "import data.database as d; print('SSL=', d.connect_args.get('sslmode'))"
    r = _roda(
        script,
        {
            "DATABASE_URL": "postgresql://u:p@db.exemplo.supabase.co:5432/postgres",
            "DEBUG": "false",
            "ADMIN_PASSWORD": "x",
        },
    )
    assert r.returncode == 0, r.stderr
    assert "SSL= require" in r.stdout


def test_sslmode_explicito_da_url_e_respeitado():
    """Não sobrescrever a escolha de quem configurou — evita conflito no libpq."""
    script = "import data.database as d; print('SSL=', d.connect_args.get('sslmode'))"
    r = _roda(
        script,
        {
            "DATABASE_URL": "postgresql://u:p@localhost:5432/db?sslmode=disable",
            "DEBUG": "false",
            "ADMIN_PASSWORD": "x",
        },
    )
    assert r.returncode == 0, r.stderr
    assert "SSL= None" in r.stdout
