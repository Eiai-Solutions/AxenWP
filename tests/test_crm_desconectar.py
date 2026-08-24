"""
Desconectar o CRM de uma instância.

Até agora só existia caminho de ida: o painel sabia DESENHAR "Nenhum CRM
conectado" e o backend inteiro sabia OPERAR nesse estado, mas nada levava até
lá. Quem conectasse o CRM errado (ou quisesse trocar) ficava preso.

O que estes testes protegem, em ordem de importância:

1. **A instância continua utilizável.** Desconectar o CRM não pode virar
   "desligar o cliente": WhatsApp, IA, leads, conversas e agente sobrevivem. Um
   desconectar que quebra o atendimento ninguém aperta.
2. **A credencial some de verdade.** Apagar o rótulo e deixar o token no banco
   seria pior que não ter o botão — `get_valid_token` continuaria devolvendo ele.
3. **Os apontadores para o CRM antigo somem.** `contact_mappings` guarda IDs de
   DENTRO da conta antiga; sobreviver faria a próxima conexão espelhar mensagem
   no contato de outra empresa.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


LOC = "wp_do_cliente"


@pytest.fixture()
def ambiente(tmp_path, monkeypatch):
    from data.database import Base
    import data.models  # noqa: F401
    from data.models import (AdminUser, ChatHistory, ContactMapping, MessageMapping,
                             QualifiedLead, Tenant)
    import services.admin_auth as auth
    import data.database as dbmod
    import auth.token_manager as tm
    import admin.dashboard as dash
    from utils.config import settings

    engine = create_engine(f"sqlite:///{tmp_path/'crm.db'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # SessionLocal é importado no topo de vários módulos: remendar só um deixa o
    # resto batendo no banco real e o teste falha longe da causa.
    for mod in (dbmod, auth, tm, dash):
        monkeypatch.setattr(mod, "SessionLocal", Session, raising=False)
    monkeypatch.setattr(settings, "debug", True, raising=False)
    from utils.limiter import limiter
    monkeypatch.setattr(limiter, "enabled", False, raising=False)

    db = Session()
    db.add(Tenant(
        location_id=LOC, company_name="Eiai Solutions", mode="ghl",
        pit_token="pit-secreto", access_token="oauth-access",
        refresh_token="oauth-refresh", token_expires_at="2030-01-01T00:00:00+00:00",
        # canais: têm que sobreviver
        whatsapp_provider="waha", waha_session=LOC,
        telegram_bot_token="tg-token",
    ))
    db.add(ContactMapping(id=f"{LOC}_5511", location_id=LOC,
                          phone_or_lid="5511999999999", ghl_contact_id="ghl-contato-1"))
    db.add(ContactMapping(id="outro_5522", location_id="outra_instancia",
                          phone_or_lid="5522888888888", ghl_contact_id="ghl-contato-2"))
    db.add(MessageMapping(zapi_message_id="z1", ghl_message_id="g1", location_id=LOC))
    db.add(MessageMapping(zapi_message_id="z2", ghl_message_id="g2",
                          location_id="outra_instancia"))
    db.add(QualifiedLead(location_id=LOC, phone="5511999999999",
                         summary="Lead quente", ghl_opportunity_id="opp-1"))
    db.add(ChatHistory(location_id=LOC, session_id=f"{LOC}_5511999999999",
                       message_type="human", content="oi"))
    db.add(AdminUser(username="luiz", password_hash=auth.hash_password("x"),
                     is_active=True, role=auth.OPERADOR))
    db.add(AdminUser(username="cliente", password_hash=auth.hash_password("y"),
                     is_active=True, role=auth.CLIENTE))
    db.commit()

    def cookie(username):
        u = db.query(AdminUser).filter_by(username=username).first()
        return auth.make_session_value(u.username, u.password_hash)

    import main

    return {
        "client": TestClient(main.app, raise_server_exceptions=False),
        "Session": Session,
        "cookie_operador": cookie("luiz"),
        "cookie_cliente": cookie("cliente"),
    }


def _desconectar(ambiente, cookie=None, loc=LOC):
    return ambiente["client"].post(
        f"/admin/tenant/{loc}/crm/desconectar",
        cookies={"admin_session": cookie or ambiente["cookie_operador"]},
        follow_redirects=False,
    )


# ── O principal: a instância sobrevive ──

def test_desconectar_nao_desliga_a_instancia(ambiente):
    """
    O medo legítimo de quem aperta o botão. Canais, leads, conversas e o flag de
    ativa continuam intactos — desconectar o CRM não é apagar o cliente.
    """
    from data.models import ChatHistory, QualifiedLead, Tenant

    assert _desconectar(ambiente).status_code == 200

    db = ambiente["Session"]()
    t = db.query(Tenant).filter_by(location_id=LOC).first()
    assert t is not None, "a instância sumiu"
    assert t.is_active is True
    assert t.whatsapp_provider == "waha" and t.waha_session == LOC, "derrubou o WhatsApp"
    assert t.telegram_bot_token == "tg-token", "derrubou o Telegram"
    assert db.query(QualifiedLead).filter_by(location_id=LOC).count() == 1, "apagou lead"
    assert db.query(ChatHistory).filter_by(location_id=LOC).count() == 1, "apagou conversa"
    db.close()


def test_o_modo_vira_whatsapp_only(ambiente):
    """
    `mode` é a chave que o resto do código lê (`handle_inbound`, `ai_gate`,
    `qualification_handler`, `agent_wizard.tem_crm`). Apagar a credencial sem
    mudar o modo deixaria o sistema tentando falar com um CRM que não autentica
    mais — falhando de fininho em cada mensagem, em vez de simplesmente não ir.
    """
    from data.models import Tenant

    _desconectar(ambiente)
    db = ambiente["Session"]()
    assert db.query(Tenant).filter_by(location_id=LOC).first().mode == "whatsapp_only"
    db.close()


def test_o_resto_do_sistema_concorda_que_nao_ha_mais_CRM(ambiente):
    """
    Não basta o campo mudar: o helper que TODO o código consulta tem que
    concordar. Se `tem_crm` continuasse True, o wizard seguiria exigindo funil de
    uma instância que não tem para onde mandar.
    """
    from data.models import Tenant
    from services.agent_wizard import tem_crm

    db = ambiente["Session"]()
    assert tem_crm(db.query(Tenant).filter_by(location_id=LOC).first()) is True
    db.close()

    _desconectar(ambiente)

    db = ambiente["Session"]()
    assert tem_crm(db.query(Tenant).filter_by(location_id=LOC).first()) is False
    db.close()


# ── A credencial some de verdade ──

def test_apaga_PIT_e_OAuth_e_nao_so_um_deles(ambiente):
    """
    São dois caminhos de autenticação alternativos. Limpar só o PIT deixaria a
    instância "desconectada" na tela e ainda autenticando por OAuth.
    """
    from data.models import Tenant

    _desconectar(ambiente)
    db = ambiente["Session"]()
    t = db.query(Tenant).filter_by(location_id=LOC).first()
    assert not t.pit_token
    assert not t.access_token
    assert not t.refresh_token
    assert not t.token_expires_at
    db.close()


@pytest.mark.asyncio
async def test_get_valid_token_para_de_devolver_credencial(ambiente):
    """
    A prova de que apagou de verdade, pelo caminho que o runtime usa.

    Antes devolve o OAuth: com os dois presentes, o `access_token` tem
    prioridade sobre o PIT — operações de conversation provider são recusadas
    com 401 para token que não pertence ao app dono do provider. (O `CLAUDE.md`
    afirmava o contrário; o código sempre foi este.)
    """
    from auth.token_manager import token_manager

    assert await token_manager.get_valid_token(LOC) == "oauth-access"
    _desconectar(ambiente)
    assert await token_manager.get_valid_token(LOC) is None, \
        "sobrou credencial depois de desconectar"


# ── Os apontadores para o CRM antigo ──

def test_apaga_os_mapeamentos_SO_desta_instancia(ambiente):
    """
    `contact_mappings` aponta para IDs de dentro da conta antiga do CRM. Guardá-los
    faria a próxima conexão espelhar mensagem em contato de outra empresa. E o
    delete tem que ser escopado: levar junto o mapeamento de outra instância
    quebraria um cliente que não pediu nada.
    """
    from data.models import ContactMapping, MessageMapping

    r = _desconectar(ambiente)
    assert r.json()["contatos_removidos"] == 1
    assert r.json()["mensagens_removidas"] == 1

    db = ambiente["Session"]()
    assert db.query(ContactMapping).filter_by(location_id=LOC).count() == 0
    assert db.query(MessageMapping).filter_by(location_id=LOC).count() == 0
    assert db.query(ContactMapping).filter_by(location_id="outra_instancia").count() == 1, \
        "apagou mapeamento de OUTRA instância"
    assert db.query(MessageMapping).filter_by(location_id="outra_instancia").count() == 1, \
        "apagou mapeamento de OUTRA instância"
    db.close()


# ── Barreira e bordas ──

def test_cliente_nao_desconecta_o_CRM_de_ninguem(ambiente):
    """`/admin` é área da equipe — e esta rota é destrutiva."""
    from data.models import Tenant

    r = _desconectar(ambiente, cookie=ambiente["cookie_cliente"])
    assert r.status_code in (401, 403)

    db = ambiente["Session"]()
    assert db.query(Tenant).filter_by(location_id=LOC).first().pit_token == "pit-secreto"
    db.close()


def test_anonimo_nao_desconecta(ambiente):
    r = ambiente["client"].post(f"/admin/tenant/{LOC}/crm/desconectar",
                                follow_redirects=False)
    assert r.status_code in (401, 403)


def test_instancia_inexistente_devolve_404_em_vez_de_500(ambiente):
    assert _desconectar(ambiente, loc="nao_existe").status_code == 404


def test_desconectar_duas_vezes_nao_quebra(ambiente):
    """
    Duplo clique, ou o operador que volta e aperta de novo. A segunda vez não tem
    o que apagar e precisa dizer isso sem erro.
    """
    primeira = _desconectar(ambiente).json()
    segunda = _desconectar(ambiente).json()
    assert primeira["tinha_credencial"] is True
    assert segunda["success"] is True
    assert segunda["tinha_credencial"] is False


# ── A fiação da tela ──

def test_o_botao_da_tela_aponta_para_a_rota_que_existe():
    """
    Backend verde e botão morto é o modo de falha silencioso desta mudança: um
    typo na URL do fetch não quebra teste nenhum, e o operador só descobre
    clicando. Aqui a URL do JS é comparada com a rota registrada de verdade.
    """
    import re

    import main

    js = open("web/static/js/dashboard.js", encoding="utf-8").read()
    achadas = re.findall(r"'(/admin/tenant/'[^;]*?crm/desconectar')", js)
    assert achadas, "o JS não chama a rota de desconectar"

    registradas = {getattr(r, "path", "") for r in main.app.routes}
    assert "/admin/tenant/{location_id}/crm/desconectar" in registradas


def test_o_botao_existe_no_markup_e_e_escondido_por_padrao():
    """
    Nasce `hidden`: quem não tem CRM conectado não pode ver um botão destrutivo
    que não faz nada. É `openInstanceSettings` que decide mostrar.
    """
    html = open("web/templates/partials/modals.html", encoding="utf-8").read()
    i = html.find('id="instance_card_crm_desconectar"')
    assert i > 0, "o botão de desconectar sumiu do modal"
    bloco = html[i:i + 600]
    assert "hidden" in bloco, "o botão não nasce escondido"
    assert "title=" in bloco, "botão sem tooltip"
    assert "desconectarCRM()" in bloco


def test_o_JS_esconde_o_botao_quando_nao_ha_CRM():
    js = open("web/static/js/dashboard.js", encoding="utf-8").read()
    i = js.find("instance_card_crm_desconectar")
    assert i > 0
    assert "!(pitToken || clientId)" in js[i:i + 400], (
        "a visibilidade não está amarrada a ter credencial"
    )
