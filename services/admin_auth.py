"""
Contas de operador do painel — usuário + senha, com hash no banco.

Antes o painel tinha UMA senha compartilhada vinda de `ADMIN_PASSWORD`, comparada
com `==`. Isso significava: nenhuma noção de quem entrou, revogar acesso de uma
pessoa exigia trocar a senha de todo mundo (e redeploy), e a senha vivia em texto
puro na env do EasyPanel.

Escolhas que valem explicar:

- **scrypt da stdlib**, não bcrypt/argon2. O Dockerfile evita instalar compilador
  de propósito — `apt-get install build-essential` estourava a memória do VPS
  (OOM) depois que o WAHA passou a dividir a máquina. `hashlib.scrypt` é um KDF
  forte que já vem no Python, sem wheel nem build.
- **O token de sessão é derivado do hash da senha**, então trocar a senha invalida
  as sessões daquele usuário automaticamente — não precisa de tabela de sessões.
- **Bootstrap pelo env**: se não existe nenhum usuário ativo, cria o primeiro a
  partir de `ADMIN_USER`/`ADMIN_PASSWORD`. Sem isso, ligar este módulo em produção
  trancaria o operador para fora do próprio painel.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timezone
from typing import Optional

from data.database import SessionLocal
from data.models import AdminUser
from utils.logger import logger

# Parâmetros interativos padrão: ~16MB de memória por verificação.
_SCRYPT_N = 16384
_SCRYPT_R = 8
_SCRYPT_P = 1
_DKLEN = 32
_SALT_BYTES = 16

_ESCOPO_SESSAO = "millochat-admin-session"

_SENHAS_FRACAS = {"admin123", "admin", "password", "123456", ""}


# --------------------------------------------------------------------------- #
# Hash de senha
# --------------------------------------------------------------------------- #
def hash_password(senha: str) -> str:
    """Formato: scrypt$n$r$p$<salt_b64>$<derivado_b64>. Salt novo a cada chamada."""
    salt = os.urandom(_SALT_BYTES)
    dk = hashlib.scrypt(
        senha.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_DKLEN
    )
    b64 = lambda b: base64.b64encode(b).decode("ascii")  # noqa: E731
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${b64(salt)}${b64(dk)}"


def verify_password(senha: str, armazenado: str) -> bool:
    """Comparação em tempo constante. Nunca levanta — hash corrompido é só `False`."""
    try:
        algo, n, r, p, salt_b64, dk_b64 = armazenado.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(
            senha.encode("utf-8"),
            salt=base64.b64decode(salt_b64),
            n=int(n), r=int(r), p=int(p),
            dklen=len(base64.b64decode(dk_b64)),
        )
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Sessão
# --------------------------------------------------------------------------- #
def make_session_value(username: str, password_hash: str) -> str:
    """
    Valor do cookie: `usuario:token`.

    O token é HMAC com CHAVE no hash da senha — trocar a senha muda o hash, o que
    invalida toda sessão viva daquele usuário sem precisar guardar sessões.
    """
    token = hmac.new(
        password_hash.encode("utf-8"),
        f"{_ESCOPO_SESSAO}:{username}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{username}:{token}"


def _buscar_usuario_sync(username: str) -> Optional[AdminUser]:
    db = SessionLocal()
    try:
        return (
            db.query(AdminUser)
            .filter(AdminUser.username == username, AdminUser.is_active.is_(True))
            .first()
        )
    finally:
        db.close()


def resolve_session(valor_cookie: Optional[str]) -> Optional[str]:
    """Devolve o username se o cookie for válido, senão None."""
    if not valor_cookie or ":" not in valor_cookie:
        return None
    # rpartition: o token é hex e nunca contém ":", então o último separador é o
    # certo. Com partition, um username com ":" gerava login 303 bem-sucedido cujo
    # cookie nunca abria o painel — trancamento silencioso, sem erro nem log.
    username, _, _token = valor_cookie.rpartition(":")
    if not username:
        return None

    usuario = _buscar_usuario_sync(username)
    if usuario is None:
        return None

    esperado = make_session_value(usuario.username, usuario.password_hash)
    return usuario.username if hmac.compare_digest(valor_cookie, esperado) else None


# --------------------------------------------------------------------------- #
# Login
# --------------------------------------------------------------------------- #
_HASH_FALSO = hash_password(secrets.token_urlsafe(16))


def authenticate(username: str, senha: str) -> Optional[AdminUser]:
    """
    Valida usuário+senha. Retorna o AdminUser ou None.

    Quando o usuário não existe, ainda assim gastamos um scrypt contra um hash
    descartável: sem isso, "usuário inexistente" responderia bem mais rápido que
    "senha errada", e dava para enumerar quem existe cronometrando o login.
    """
    usuario = _buscar_usuario_sync((username or "").strip())
    if usuario is None:
        verify_password(senha or "", _HASH_FALSO)
        return None

    if not verify_password(senha or "", usuario.password_hash):
        return None

    db = SessionLocal()
    try:
        alvo = db.query(AdminUser).filter(AdminUser.id == usuario.id).first()
        if alvo:
            alvo.last_login_at = datetime.now(timezone.utc)
            db.commit()
    except Exception as e:  # marcar o último acesso nunca pode impedir o login
        logger.warning(f"[AUTH] Falha ao gravar last_login_at de '{username}': {e}")
        db.rollback()
    finally:
        db.close()

    return usuario


def set_password(username: str, nova_senha: str) -> bool:
    """Troca a senha (e, por tabela, derruba as sessões vivas do usuário)."""
    db = SessionLocal()
    try:
        usuario = db.query(AdminUser).filter(AdminUser.username == username).first()
        if usuario is None:
            return False
        usuario.password_hash = hash_password(nova_senha)
        db.commit()
        logger.info(f"[AUTH] Senha alterada para '{username}'; sessões antigas invalidadas.")
        return True
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Bootstrap
# --------------------------------------------------------------------------- #
def bootstrap_admin_user() -> None:
    """
    Cria o primeiro operador a partir do env, se ainda não houver nenhum ativo.

    Roda no lifespan. É o que impede que ligar login-por-usuário tranque o
    operador para fora: enquanto ADMIN_PASSWORD existir, sempre há como entrar.
    Não mexe em usuário já existente — trocar ADMIN_PASSWORD no env NÃO altera a
    senha de quem já está no banco (senão o env viraria a fonte da verdade de novo).
    """
    from utils.config import settings

    db = SessionLocal()
    try:
        if db.query(AdminUser).filter(AdminUser.is_active.is_(True)).count() > 0:
            return

        # `.strip()` igual ao do username logo abaixo: colar a senha no campo web
        # do EasyPanel arrasta espaço/quebra de linha com facilidade, e o hash
        # ficaria de "senha " enquanto o operador digita "senha". Como o bootstrap
        # não reexecuta com usuário já criado, corrigir a env depois não resolveria.
        senha = (settings.admin_password or "").strip()
        if not senha:
            logger.error(
                "[AUTH] Nenhum operador cadastrado e ADMIN_PASSWORD vazio — o painel "
                "ficará INACESSÍVEL. Defina ADMIN_PASSWORD e reinicie. (Os webhooks "
                "seguem funcionando, por isso isto não derruba o app.)"
            )
            return

        if senha in _SENHAS_FRACAS:
            logger.warning(
                "[AUTH] ADMIN_PASSWORD é uma senha fraca conhecida. O primeiro "
                "operador será criado com ela — troque assim que entrar."
            )

        username = (settings.admin_user or "admin").strip()

        # Pode existir uma conta com este nome, porém DESATIVADA — inserir de novo
        # violaria a unique de `username` e deixaria o painel sem ninguém para
        # entrar. Como só chegamos aqui com ZERO operadores ativos, o sistema já
        # está inacessível: reativar pelo env é justamente a rota de recuperação
        # (quem controla o deploy controla o env). Com qualquer operador ativo,
        # nada disso roda — desativar um operador continua valendo.
        existente = db.query(AdminUser).filter(AdminUser.username == username).first()
        if existente is not None:
            existente.is_active = True
            existente.password_hash = hash_password(senha)
            db.commit()
            logger.warning(
                f"[AUTH] Nenhum operador ativo: conta '{username}' reativada e senha "
                "redefinida pelo ADMIN_PASSWORD (recuperação de acesso)."
            )
            return

        db.add(AdminUser(username=username, password_hash=hash_password(senha), is_active=True))
        db.commit()
        logger.info(f"[AUTH] Operador inicial '{username}' criado a partir do ADMIN_PASSWORD.")
    except Exception as e:
        logger.error(f"[AUTH] Falha no bootstrap do operador: {e}", exc_info=True)
        db.rollback()
    finally:
        db.close()
