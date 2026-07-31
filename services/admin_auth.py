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
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from data.database import SessionLocal
from data.models import AdminUser, Organization, Tenant
from utils.logger import logger

# Papéis. 'operator' = equipe Eiai (vê tudo). 'client' = dono da conta.
OPERADOR = "operator"
CLIENTE = "client"

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


@dataclass(frozen=True)
class Principal:
    """
    Quem está pedindo, e o que ele alcança.

    `locations` é a lista de `location_id` permitidos, lida do BANCO a cada
    request — de propósito. Se o escopo viajasse no cookie, tirar um tenant de um
    cliente só valeria depois que ele trocasse a senha, porque o token é HMAC
    sobre (username, password_hash). Revogação precisa ser imediata.

    Operador tem `locations` vazio e `is_operator=True`: ele não é escopado, e a
    ausência de escopo NUNCA deve ser lida como "vazio = nenhum" nem como
    "vazio = todos" por engano — sempre cheque `is_operator` explicitamente.
    """

    username: str
    role: str
    organization_id: Optional[int]
    locations: tuple[str, ...]

    @property
    def is_operator(self) -> bool:
        return self.role == OPERADOR

    def alcanca(self, location_id: Optional[str]) -> bool:
        """Única fonte da verdade sobre 'esse principal pode tocar neste tenant?'."""
        if self.is_operator:
            return True
        return bool(location_id) and location_id in self.locations


def _locations_da_org(db, organization_id: Optional[int]) -> tuple[str, ...]:
    if organization_id is None:
        return ()
    linhas = (
        db.query(Tenant.location_id)
        .filter(Tenant.organization_id == organization_id)
        .all()
    )
    return tuple(x[0] for x in linhas)


def resolve_principal(valor_cookie: Optional[str]) -> Optional[Principal]:
    """Valida o cookie e monta o principal. None = não autenticado."""
    if not valor_cookie or ":" not in valor_cookie:
        return None
    username, _, _token = valor_cookie.rpartition(":")
    if not username:
        return None

    db = SessionLocal()
    try:
        usuario = (
            db.query(AdminUser)
            .filter(AdminUser.username == username, AdminUser.is_active.is_(True))
            .first()
        )
        if usuario is None:
            return None

        esperado = make_session_value(usuario.username, usuario.password_hash)
        if not hmac.compare_digest(valor_cookie, esperado):
            return None

        papel = (usuario.role or OPERADOR).strip() or OPERADOR
        org = usuario.organization_id
        return Principal(
            username=usuario.username,
            role=papel,
            organization_id=org,
            locations=() if papel == OPERADOR else _locations_da_org(db, org),
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


# --------------------------------------------------------------------------- #
# Gestão de operadores
# --------------------------------------------------------------------------- #
# Regras de nome: ":" quebraria o parse do cookie (`usuario:token`); os demais
# limites evitam nome com espaço/acento que o operador digita errado no login.
_USERNAME_RE = re.compile(r"^[a-zA-Z0-9._-]{3,64}$")


def listar_usuarios() -> list[dict]:
    """Dados de exibição. Nunca devolve `password_hash` — ele É a chave da sessão."""
    db = SessionLocal()
    try:
        return [
            {
                "username": u.username,
                "is_active": bool(u.is_active),
                "created_at": u.created_at,
                "last_login_at": u.last_login_at,
            }
            for u in db.query(AdminUser).order_by(AdminUser.username).all()
        ]
    finally:
        db.close()


def _ativos_alem_de(db, username: str) -> int:
    return (
        db.query(AdminUser)
        .filter(AdminUser.is_active.is_(True), AdminUser.username != username)
        .count()
    )


def criar_usuario(username: str, senha: str) -> tuple[bool, str]:
    """Cria operador. Devolve (ok, mensagem)."""
    username = (username or "").strip()
    senha = senha or ""

    if not _USERNAME_RE.match(username):
        return False, "Usuário deve ter 3-64 caracteres: letras, números, ponto, hífen ou _."
    if len(senha) < 8:
        return False, "A senha precisa de pelo menos 8 caracteres."

    db = SessionLocal()
    try:
        if db.query(AdminUser).filter(AdminUser.username == username).first():
            return False, f"Já existe um operador '{username}'."
        db.add(AdminUser(username=username, password_hash=hash_password(senha), is_active=True))
        db.commit()
        logger.info(f"[AUTH] Operador '{username}' criado.")
        return True, f"Operador '{username}' criado."
    except Exception as e:
        db.rollback()
        # `{e}` cru do SQLAlchemy traz "[SQL: INSERT ...] [parameters: (...)]" —
        # ou seja, o password_hash iria parar no log do EasyPanel.
        logger.error(f"[AUTH] Falha ao criar operador '{username}': {type(e).__name__}")
        return False, "Não foi possível criar o operador."
    finally:
        db.close()


def definir_ativo(username: str, ativo: bool, quem_pediu: str) -> tuple[bool, str]:
    """
    Ativa/desativa operador.

    Duas travas contra o painel se trancar sozinho — este é o ponto onde um clique
    distraído custa o acesso de todo mundo:
      1. Ninguém se desativa (é o erro mais fácil de cometer e o mais irreversível).
      2. Nunca sobra zero operador ativo.
    """
    username = (username or "").strip()

    db = SessionLocal()
    try:
        # Serializa desativações concorrentes. Sem o lock, dois operadores clicando
        # "desativar" ao mesmo tempo veriam, cada um, o OUTRO como ativo (READ
        # COMMITTED não enxerga a transação alheia ainda não commitada), os dois
        # passariam na checagem e sobrariam ZERO ativos — trancamento total. A
        # tabela tem punhado de linhas, então travar todas custa nada.
        if not ativo and db.get_bind().dialect.name == "postgresql":
            db.query(AdminUser).with_for_update().all()

        usuario = db.query(AdminUser).filter(AdminUser.username == username).first()
        if usuario is None:
            return False, "Operador não encontrado."

        # Compara o nome RESOLVIDO no banco, não o texto do formulário: se a
        # coluna tiver collation case-insensitive, "ADMIN" acharia a linha de
        # "admin" e passaria por uma comparação feita contra o input cru.
        if not ativo and usuario.username == quem_pediu:
            return False, "Você não pode desativar a própria conta."

        if not ativo and _ativos_alem_de(db, usuario.username) == 0:
            return False, "Este é o último operador ativo — o painel ficaria inacessível."

        usuario.is_active = bool(ativo)
        db.flush()

        # Confere a invariante já com a mudança aplicada na transação. É a rede
        # embaixo do lock: se por qualquer motivo sobrarem zero ativos, desfaz.
        if db.query(AdminUser).filter(AdminUser.is_active.is_(True)).count() == 0:
            db.rollback()
            return False, "Este é o último operador ativo — o painel ficaria inacessível."

        db.commit()
        acao = "reativado" if ativo else "desativado"
        logger.info(f"[AUTH] Operador '{usuario.username}' {acao} por '{quem_pediu}'.")
        return True, f"Operador '{usuario.username}' {acao}."
    except Exception as e:
        # Sem isto, um soluço do pooler devolvia 500 cru no meio de uma ação de
        # acesso: o operador não saberia se desativou o colega ou não, e reenviaria
        # às cegas. `criar_usuario` já tratava; estas duas não.
        db.rollback()
        logger.error(f"[AUTH] Falha ao alterar '{username}': {type(e).__name__}")
        return False, "Não foi possível concluir — tente de novo."
    finally:
        db.close()


def redefinir_senha_de_outro(alvo: str, nova_senha: str, quem_pediu: str) -> tuple[bool, str]:
    """
    Redefine a senha de OUTRO operador.

    A trava contra `alvo == quem_pediu` mora aqui, não na rota, pelo mesmo motivo
    das travas de `definir_ativo`: a regra precisa valer por qualquer caminho.

    Sem ela a rota furava a defesa que `/admin/senha` documenta — lá a troca da
    própria senha exige a senha ATUAL, justamente para que um cookie roubado não
    tranque o dono para fora. Redefinir "outro" apontando para si mesmo pulava
    essa exigência, e um typo na própria senha era irreversível: a conta continua
    ATIVA, então o bootstrap do próximo deploy não reseta nada e a recuperação
    viraria UPDATE manual no banco.
    """
    alvo = (alvo or "").strip()

    if alvo == (quem_pediu or "").strip():
        return False, "Para trocar a SUA senha use 'Acesso ao painel' — ele confirma a senha atual."
    if len(nova_senha or "") < 8:
        return False, "A senha precisa de pelo menos 8 caracteres."

    db = SessionLocal()
    try:
        usuario = db.query(AdminUser).filter(AdminUser.username == alvo).first()
        if usuario is None:
            return False, "Operador não encontrado."
        # Compara o nome resolvido, não o texto do form: com collation
        # case-insensitive, "ADMIN" acharia a linha de "admin" e escaparia.
        if usuario.username == (quem_pediu or "").strip():
            return False, "Para trocar a SUA senha use 'Acesso ao painel' — ele confirma a senha atual."

        usuario.password_hash = hash_password(nova_senha)
        db.commit()
        logger.info(f"[AUTH] Senha de '{usuario.username}' redefinida por '{quem_pediu}'.")
        return True, f"Senha de '{usuario.username}' redefinida. As sessões dele foram encerradas."
    except Exception as e:
        db.rollback()
        logger.error(f"[AUTH] Falha ao redefinir senha de '{alvo}': {type(e).__name__}")
        return False, "Não foi possível concluir — tente de novo."
    finally:
        db.close()


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
    except Exception as e:
        db.rollback()
        logger.error(f"[AUTH] Falha ao trocar senha de '{username}': {type(e).__name__}")
        return False
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
        ativos = db.query(AdminUser).filter(AdminUser.is_active.is_(True)).count()

        # Resgate explícito. Sem isto, "trocar ADMIN_PASSWORD e redeployar" — o
        # reflexo natural de quem perdeu o acesso, e o que a documentação do
        # projeto mandava fazer — NÃO surtia efeito nenhum: com a conta ativa o
        # bootstrap retornava cedo e a senha do banco continuava valendo. Foi
        # exatamente o que aconteceu em produção em 2026-07-31.
        # É opt-in de propósito: ligado sempre, o env voltaria a sobrescrever o
        # banco a cada deploy e a troca de senha pelo painel não sobreviveria.
        if settings.admin_force_reset:
            senha = (settings.admin_password or "").strip()
            username = (settings.admin_user or "admin").strip()
            if not senha or not _USERNAME_RE.match(username):
                logger.error("[AUTH] ADMIN_FORCE_RESET ligado, mas ADMIN_USER/ADMIN_PASSWORD inválidos.")
                return
            usuario = db.query(AdminUser).filter(AdminUser.username == username).first()
            if usuario is None:
                db.add(AdminUser(username=username, password_hash=hash_password(senha), is_active=True))
            else:
                usuario.password_hash = hash_password(senha)
                usuario.is_active = True
            db.commit()
            logger.warning(
                f"[AUTH] ADMIN_FORCE_RESET: senha de '{username}' redefinida pelo env e conta "
                "ativada. DESLIGUE a variável e reinicie — enquanto ela estiver ligada, todo "
                "deploy sobrescreve a senha."
            )
            return

        if ativos > 0:
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
        if not _USERNAME_RE.match(username):
            logger.error(
                f"[AUTH] ADMIN_USER '{username[:32]}' é inválido (use 3-64 de "
                "[a-zA-Z0-9._-]). Nenhum operador criado."
            )
            return

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
