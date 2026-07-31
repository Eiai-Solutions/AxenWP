"""
Login do painel por usuário + senha.

Antes era UMA senha compartilhada de env, comparada com `==`. Estes testes
travam as propriedades que a troca precisa garantir — em especial as que, se
quebrarem, ou trancam o operador para fora ou deixam o painel aberto.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def banco(tmp_path, monkeypatch):
    """Banco SQLite isolado por teste, injetado no SessionLocal do módulo."""
    from data.database import Base
    import data.models  # noqa: F401 — registra os modelos no metadata
    import services.admin_auth as auth

    engine = create_engine(f"sqlite:///{tmp_path/'auth.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(auth, "SessionLocal", Session, raising=True)
    return Session


# --------------------------------------------------------------------------- #
# Hash
# --------------------------------------------------------------------------- #
def test_hash_confere_a_senha_certa_e_recusa_a_errada():
    from services.admin_auth import hash_password, verify_password

    h = hash_password("senha-forte-123")
    assert verify_password("senha-forte-123", h)
    assert not verify_password("senha-forte-124", h)
    assert not verify_password("", h)


def test_a_senha_nunca_aparece_no_hash():
    from services.admin_auth import hash_password

    assert "senha-secreta" not in hash_password("senha-secreta")


def test_salt_novo_a_cada_hash():
    """Hashes iguais entregariam que dois operadores usam a mesma senha."""
    from services.admin_auth import hash_password

    assert hash_password("mesma") != hash_password("mesma")


def test_hash_corrompido_devolve_false_em_vez_de_explodir():
    """Um 500 no login por hash inválido viraria oráculo e derrubaria a tela."""
    from services.admin_auth import verify_password

    for lixo in ["", "nao-e-um-hash", "scrypt$abc", "bcrypt$1$2$3$4$5", "$$$$$"]:
        assert verify_password("x", lixo) is False


# --------------------------------------------------------------------------- #
# Sessão
# --------------------------------------------------------------------------- #
def test_trocar_a_senha_invalida_a_sessao_viva(banco):
    """A propriedade que dispensa tabela de sessões — se cair, revogar não revoga."""
    from services.admin_auth import hash_password, make_session_value, resolve_session, set_password
    from data.models import AdminUser

    db = banco()
    db.add(AdminUser(username="luiz", password_hash=hash_password("antiga"), is_active=True))
    db.commit()
    h = db.query(AdminUser).filter_by(username="luiz").first().password_hash
    db.close()

    cookie = make_session_value("luiz", h)
    assert resolve_session(cookie) == "luiz"

    set_password("luiz", "nova")
    assert resolve_session(cookie) is None, "sessão sobreviveu à troca de senha"


def test_desativar_usuario_derruba_a_sessao(banco):
    from services.admin_auth import hash_password, make_session_value, resolve_session
    from data.models import AdminUser

    db = banco()
    db.add(AdminUser(username="luiz", password_hash=hash_password("s"), is_active=True))
    db.commit()
    h = db.query(AdminUser).filter_by(username="luiz").first().password_hash
    cookie = make_session_value("luiz", h)
    assert resolve_session(cookie) == "luiz"

    db.query(AdminUser).filter_by(username="luiz").first().is_active = False
    db.commit()
    db.close()
    assert resolve_session(cookie) is None


@pytest.mark.parametrize(
    "cookie",
    [None, "", "sem-dois-pontos", ":sotoken", "luiz:", "luiz:tokenerrado", "outro:x"],
)
def test_cookie_malformado_ou_forjado_nao_autentica(banco, cookie):
    from services.admin_auth import hash_password, resolve_session
    from data.models import AdminUser

    db = banco()
    db.add(AdminUser(username="luiz", password_hash=hash_password("s"), is_active=True))
    db.commit()
    db.close()
    assert resolve_session(cookie) is None


# --------------------------------------------------------------------------- #
# Login
# --------------------------------------------------------------------------- #
def test_login_valido_devolve_o_usuario(banco):
    from services.admin_auth import authenticate, hash_password
    from data.models import AdminUser

    db = banco()
    db.add(AdminUser(username="luiz", password_hash=hash_password("boa-senha"), is_active=True))
    db.commit()
    db.close()

    assert authenticate("luiz", "boa-senha").username == "luiz"
    assert authenticate("luiz", "senha-errada") is None
    assert authenticate("naoexiste", "boa-senha") is None


def test_usuario_inativo_nao_loga(banco):
    from services.admin_auth import authenticate, hash_password
    from data.models import AdminUser

    db = banco()
    db.add(AdminUser(username="ex", password_hash=hash_password("s"), is_active=False))
    db.commit()
    db.close()
    assert authenticate("ex", "s") is None


def test_senha_certa_de_outro_usuario_nao_serve(banco):
    """Guarda contra checar a senha sem amarrar ao usuário."""
    from services.admin_auth import authenticate, hash_password
    from data.models import AdminUser

    db = banco()
    db.add(AdminUser(username="a", password_hash=hash_password("senha-de-a"), is_active=True))
    db.add(AdminUser(username="b", password_hash=hash_password("senha-de-b"), is_active=True))
    db.commit()
    db.close()
    assert authenticate("a", "senha-de-b") is None
    assert authenticate("b", "senha-de-a") is None


# --------------------------------------------------------------------------- #
# Bootstrap — o que impede tranca-fora
# --------------------------------------------------------------------------- #
def test_bootstrap_cria_o_primeiro_operador_do_env(banco, monkeypatch):
    from utils.config import settings
    from services.admin_auth import authenticate, bootstrap_admin_user
    from data.models import AdminUser

    monkeypatch.setattr(settings, "admin_user", "luiz", raising=False)
    monkeypatch.setattr(settings, "admin_password", "senha-do-env", raising=False)

    bootstrap_admin_user()

    db = banco()
    assert db.query(AdminUser).count() == 1
    db.close()
    assert authenticate("luiz", "senha-do-env") is not None


def test_bootstrap_e_idempotente_e_nao_sobrescreve_senha_existente(banco, monkeypatch):
    """Trocar ADMIN_PASSWORD no env não pode redefinir a senha de quem já existe."""
    from utils.config import settings
    from services.admin_auth import authenticate, bootstrap_admin_user, hash_password
    from data.models import AdminUser

    db = banco()
    db.add(AdminUser(username="luiz", password_hash=hash_password("a-que-vale"), is_active=True))
    db.commit()
    db.close()

    monkeypatch.setattr(settings, "admin_user", "luiz", raising=False)
    monkeypatch.setattr(settings, "admin_password", "outra-do-env", raising=False)
    bootstrap_admin_user()
    bootstrap_admin_user()

    db = banco()
    assert db.query(AdminUser).count() == 1
    db.close()
    assert authenticate("luiz", "a-que-vale") is not None
    assert authenticate("luiz", "outra-do-env") is None


def test_bootstrap_sem_senha_no_env_nao_cria_nem_explode(banco, monkeypatch):
    """Sem ADMIN_PASSWORD o painel fica inacessível, mas o app (webhooks) sobe."""
    from utils.config import settings
    from services.admin_auth import bootstrap_admin_user
    from data.models import AdminUser

    monkeypatch.setattr(settings, "admin_password", "", raising=False)
    bootstrap_admin_user()

    db = banco()
    assert db.query(AdminUser).count() == 0
    db.close()


def test_bootstrap_recupera_acesso_quando_so_ha_conta_desativada(banco, monkeypatch):
    """
    Sem isto, o INSERT colidiria na unique de `username` e o painel ficaria sem
    ninguém para entrar. Só vale com ZERO operadores ativos — é rota de recuperação.
    """
    from utils.config import settings
    from services.admin_auth import authenticate, bootstrap_admin_user, hash_password
    from data.models import AdminUser

    db = banco()
    db.add(AdminUser(username="admin", password_hash=hash_password("antiga"), is_active=False))
    db.commit()
    db.close()

    monkeypatch.setattr(settings, "admin_user", "admin", raising=False)
    monkeypatch.setattr(settings, "admin_password", "resgate", raising=False)
    bootstrap_admin_user()

    db = banco()
    assert db.query(AdminUser).count() == 1, "criou conta duplicada em vez de reativar"
    db.close()
    assert authenticate("admin", "resgate") is not None


def test_desativar_um_operador_nao_o_ressuscita_se_houver_outro_ativo(banco, monkeypatch):
    """A recuperação não pode virar bypass do 'desativar usuário'."""
    from utils.config import settings
    from services.admin_auth import authenticate, bootstrap_admin_user, hash_password
    from data.models import AdminUser

    db = banco()
    db.add(AdminUser(username="demitido", password_hash=hash_password("s"), is_active=False))
    db.add(AdminUser(username="luiz", password_hash=hash_password("s"), is_active=True))
    db.commit()
    db.close()

    monkeypatch.setattr(settings, "admin_user", "demitido", raising=False)
    monkeypatch.setattr(settings, "admin_password", "s", raising=False)
    bootstrap_admin_user()

    assert authenticate("demitido", "s") is None, "conta desativada foi ressuscitada"


def test_bootstrap_ignora_espaco_colado_na_senha_do_env(banco, monkeypatch):
    """
    Colar a senha no campo web do EasyPanel arrasta espaço/quebra de linha. Sem
    strip, o hash seria de "senha \n" e o operador nunca entraria — e corrigir a
    env depois não resolveria, porque o bootstrap não reexecuta com usuário ativo.
    """
    from utils.config import settings
    from services.admin_auth import authenticate, bootstrap_admin_user

    monkeypatch.setattr(settings, "admin_user", "admin", raising=False)
    monkeypatch.setattr(settings, "admin_password", "  senha-com-espaco \n", raising=False)
    bootstrap_admin_user()

    assert authenticate("admin", "senha-com-espaco") is not None


# --------------------------------------------------------------------------- #
# Gestão de operadores — a invariante é "nunca sobra zero ativo"
# --------------------------------------------------------------------------- #
def test_nao_da_para_desativar_o_ultimo_operador_ativo(banco):
    """A trava principal: sem ela, um clique deixa o painel inacessível para sempre."""
    from services.admin_auth import criar_usuario, definir_ativo, listar_usuarios

    criar_usuario("luiz", "senha-longa-1")
    ok, msg = definir_ativo("luiz", False, quem_pediu="outro")
    assert ok is False
    assert "último operador ativo" in msg
    assert listar_usuarios()[0]["is_active"] is True


def test_ninguem_desativa_a_propria_conta(banco):
    from services.admin_auth import criar_usuario, definir_ativo

    criar_usuario("luiz", "senha-longa-1")
    criar_usuario("maria", "senha-longa-2")
    ok, msg = definir_ativo("luiz", False, quem_pediu="luiz")
    assert ok is False
    assert "própria conta" in msg


def test_desativar_funciona_quando_sobra_outro_ativo(banco):
    from services.admin_auth import authenticate, criar_usuario, definir_ativo

    criar_usuario("luiz", "senha-longa-1")
    criar_usuario("maria", "senha-longa-2")
    ok, _ = definir_ativo("maria", False, quem_pediu="luiz")
    assert ok is True
    assert authenticate("maria", "senha-longa-2") is None
    assert authenticate("luiz", "senha-longa-1") is not None


def test_reativar_devolve_o_acesso(banco):
    from services.admin_auth import authenticate, criar_usuario, definir_ativo

    criar_usuario("luiz", "senha-longa-1")
    criar_usuario("maria", "senha-longa-2")
    definir_ativo("maria", False, quem_pediu="luiz")
    definir_ativo("maria", True, quem_pediu="luiz")
    assert authenticate("maria", "senha-longa-2") is not None


@pytest.mark.parametrize(
    "nome", ["ab", "luiz:ops", "com espaco", "joão", "", "a" * 65, "usuario@x"]
)
def test_username_invalido_e_recusado(banco, nome):
    """':' quebraria o parse do cookie; o resto evita nome que o operador erra ao digitar."""
    from services.admin_auth import criar_usuario

    ok, _ = criar_usuario(nome, "senha-longa-1")
    assert ok is False


def test_senha_curta_e_recusada_na_criacao(banco):
    from services.admin_auth import criar_usuario

    ok, msg = criar_usuario("luiz", "curta")
    assert ok is False and "8 caracteres" in msg


def test_nao_cria_operador_duplicado(banco):
    from services.admin_auth import criar_usuario, listar_usuarios

    assert criar_usuario("luiz", "senha-longa-1")[0] is True
    ok, msg = criar_usuario("luiz", "outra-senha-1")
    assert ok is False and "Já existe" in msg
    assert len(listar_usuarios()) == 1


def test_listar_nunca_devolve_o_hash(banco):
    """O hash É a chave da sessão — vazá-lo no template seria entregar o painel."""
    from services.admin_auth import criar_usuario, listar_usuarios

    criar_usuario("luiz", "senha-longa-1")
    for u in listar_usuarios():
        assert "password_hash" not in u
        assert not any("scrypt" in str(v) for v in u.values())


def test_redefinir_senha_de_outro_derruba_a_sessao_dele(banco):
    from services.admin_auth import (
        criar_usuario, make_session_value, resolve_session, set_password, _buscar_usuario_sync,
    )

    criar_usuario("maria", "senha-longa-2")
    u = _buscar_usuario_sync("maria")
    cookie = make_session_value(u.username, u.password_hash)
    assert resolve_session(cookie) == "maria"

    set_password("maria", "senha-redefinida")
    assert resolve_session(cookie) is None


def test_auto_desativacao_e_barrada_pelo_nome_resolvido_no_banco(banco):
    """
    A trava compara o username vindo do BANCO, não o texto do form: com collation
    case-insensitive, "ADMIN" acharia a linha de "admin" e escaparia de uma
    comparação feita contra o input cru.
    """
    from services.admin_auth import criar_usuario, definir_ativo, listar_usuarios

    criar_usuario("admin", "senha-longa-1")
    criar_usuario("maria", "senha-longa-2")

    # espaços em volta são normalizados e a trava continua valendo
    ok, msg = definir_ativo("  admin  ", False, quem_pediu="admin")
    assert ok is False and "própria conta" in msg
    assert [u for u in listar_usuarios() if u["username"] == "admin"][0]["is_active"] is True


def test_desativar_operador_inexistente_nao_altera_ninguem(banco):
    from services.admin_auth import criar_usuario, definir_ativo, listar_usuarios

    criar_usuario("luiz", "senha-longa-1")
    ok, msg = definir_ativo("fantasma", False, quem_pediu="luiz")
    assert ok is False and "não encontrado" in msg
    assert all(u["is_active"] for u in listar_usuarios())


def test_redefinir_a_propria_senha_pela_via_de_outro_e_recusado(banco):
    from services.admin_auth import authenticate, criar_usuario, redefinir_senha_de_outro

    criar_usuario("luiz", "senha-longa-1")
    ok, msg = redefinir_senha_de_outro("luiz", "senha-nova-1", quem_pediu="luiz")
    assert ok is False and "SUA senha" in msg
    assert authenticate("luiz", "senha-longa-1") is not None


def test_redefinir_senha_de_outro_funciona(banco):
    from services.admin_auth import authenticate, criar_usuario, redefinir_senha_de_outro

    criar_usuario("luiz", "senha-longa-1")
    criar_usuario("maria", "senha-longa-2")
    ok, _ = redefinir_senha_de_outro("maria", "senha-nova-2", quem_pediu="luiz")
    assert ok is True
    assert authenticate("maria", "senha-nova-2") is not None
    assert authenticate("maria", "senha-longa-2") is None


def test_bootstrap_recusa_ADMIN_USER_invalido(banco, monkeypatch):
    """O env era o único caminho que escapava do regex que o resto do módulo aplica."""
    from utils.config import settings
    from services.admin_auth import bootstrap_admin_user, listar_usuarios

    monkeypatch.setattr(settings, "admin_user", "luiz:ops", raising=False)
    monkeypatch.setattr(settings, "admin_password", "senha-longa-1", raising=False)
    bootstrap_admin_user()
    assert listar_usuarios() == []


def test_falha_ao_criar_operador_nao_derrama_o_hash_no_log(banco, monkeypatch, caplog):
    """
    A exceção crua do SQLAlchemy carrega "[parameters: (...)]" — com o hash dentro.
    O log do EasyPanel é visível a quem não tem acesso ao banco.
    """
    import logging
    import services.admin_auth as auth

    original = auth.SessionLocal
    HASH_FALSO = "scrypt$16384$8$1$c2FsdA==$ZGVyaXZhZG8="

    def sessao_que_falha_no_commit():
        db = original()
        def commit_explode():
            raise RuntimeError(
                "(psycopg2.errors.UniqueViolation) duplicate key\n"
                "[SQL: INSERT INTO admin_users ...]\n"
                f"[parameters: ('luiz', '{HASH_FALSO}', 1)]"
            )
        db.commit = commit_explode
        return db

    monkeypatch.setattr(auth, "SessionLocal", sessao_que_falha_no_commit, raising=True)
    with caplog.at_level(logging.ERROR):
        ok, msg = auth.criar_usuario("luiz", "senha-longa-1")

    assert ok is False
    assert "scrypt$" not in caplog.text, f"hash vazou para o log: {caplog.text[:300]}"
    assert "parameters" not in caplog.text
