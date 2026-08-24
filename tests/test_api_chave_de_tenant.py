"""
A chave de API por tenant — a porta pela qual um CRM de terceiro entra.

O que este arquivo existe para garantir, em ordem de importância:

1. **Uma chave só alcança o próprio tenant.** O `location_id` vem da chave, nunca
   do caminho — é o que faz a classe de bug "chave de A mexe em B" não existir em
   vez de virar checagem que alguém esquece numa rota nova.
2. **A chave não é recuperável.** Guardamos o SHA-256; nem nós conseguimos
   devolvê-la depois. Chave recuperável é chave que vaza duas vezes.
3. **Recusar não vira oráculo.** Chave inexistente, revogada e tenant inativo
   respondem a mesma coisa.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from data.models import AIAgent, Base, ConversationAIState, Tenant, TenantApiKey

LOC_A = "wp_aaaaaaaaaaaa"
LOC_B = "wp_bbbbbbbbbbbb"


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    import data.database as dbmod
    import services.admin_auth as auth
    import services.tenant_auth as ta
    from data.models import AdminUser
    from utils.config import settings
    from utils.limiter import limiter

    engine = create_engine(f"sqlite:///{tmp_path}/api.db",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    # `SessionLocal` é importado no TOPO de vários módulos, então cada um guarda a
    # própria referência e patchar só `data.database` não os alcança. Rodando este
    # arquivo sozinho passava (o SQLite default existia); na suíte inteira, o
    # /health caía em 500 com "no such table". Patch em todos os que a requisição
    # atravessa.
    import admin.ai_agent as aa
    import auth.token_manager as tm
    import main as mainmod

    for mod in (dbmod, auth, aa, tm, mainmod):
        monkeypatch.setattr(mod, "SessionLocal", Session, raising=True)
    monkeypatch.setattr(settings, "debug", True, raising=False)
    monkeypatch.setattr(limiter, "enabled", False, raising=False)

    db = Session()
    for loc, nome in ((LOC_A, "Cliente A"), (LOC_B, "Cliente B")):
        db.add(Tenant(location_id=loc, company_name=nome, mode="whatsapp_only",
                      is_active=True))
        db.add(AIAgent(location_id=loc, channel="whatsapp", name="Ellen",
                       prompt="p", model="m", is_active=True))
    db.add(AdminUser(username="op", password_hash=auth.hash_password("senha-do-operador"),
                     is_active=True, role=auth.OPERADOR))
    db.commit()
    u = db.query(AdminUser).filter_by(username="op").first()
    cookie = auth.make_session_value(u.username, u.password_hash)
    db.close()

    import main
    return SimpleNamespace(
        c=TestClient(main.app, raise_server_exceptions=False),
        cookie=cookie, Session=Session, ta=ta,
    )


def _criar_chave(a, loc=LOC_A, nome="Manager"):
    r = a.c.post(f"/admin/agents/{loc}/api-keys", json={"nome": nome},
                 cookies={"admin_session": a.cookie})
    return r.json()


def _get(a, caminho, chave, **kw):
    return a.c.get(caminho, headers={"Authorization": f"Bearer {chave}"}, **kw)


def _post(a, caminho, chave, corpo):
    return a.c.post(caminho, json=corpo, headers={"Authorization": f"Bearer {chave}"})


# ── Criar e usar ──

def test_a_chave_aparece_UMA_vez_e_nunca_mais(ambiente):
    r = _criar_chave(ambiente)
    assert r["success"] is True
    chave = r["chave"]
    assert chave.startswith("mc_live_")

    listagem = ambiente.c.get(f"/admin/agents/{LOC_A}/api-keys",
                              cookies={"admin_session": ambiente.cookie}).json()
    bruto = str(listagem)
    assert chave not in bruto, "a listagem devolveu a chave em claro"
    assert listagem["chaves"][0]["prefixo"] == chave[:16]


def test_a_chave_em_claro_NAO_e_gravada(ambiente):
    """Guardamos o hash. Nem nós conseguimos recuperá-la — é esse o ponto."""
    chave = _criar_chave(ambiente)["chave"]

    db = ambiente.Session()
    try:
        (linha,) = db.query(TenantApiKey).all()
        assert linha.key_hash != chave
        assert len(linha.key_hash) == 64          # sha256 hex
        assert chave not in str(linha.__dict__)
    finally:
        db.close()


def test_a_chave_identifica_o_tenant_sozinha(ambiente):
    chave = _criar_chave(ambiente)["chave"]
    r = _get(ambiente, "/api/v1/me", chave)
    assert r.status_code == 200
    assert r.json()["location_id"] == LOC_A
    assert r.json()["empresa"] == "Cliente A"


# ── Isolamento entre tenants: a propriedade central ──

def test_uma_chave_NAO_alcanca_outro_tenant(ambiente):
    """
    O `location_id` vem da chave, não do caminho — então nem há caminho a forjar.
    Este teste existe para o dia em que alguém 'melhorar' a rota aceitando o
    tenant pela URL.
    """
    chave_a = _criar_chave(ambiente, LOC_A)["chave"]

    _post(ambiente, "/api/v1/conversations/5547/ai", chave_a,
          {"enabled": False, "channel": "whatsapp"})

    db = ambiente.Session()
    try:
        (estado,) = db.query(ConversationAIState).all()
        assert estado.location_id == LOC_A, "a chave de A escreveu no tenant errado"
    finally:
        db.close()


def test_nenhuma_rota_da_api_aceita_location_id_no_caminho(ambiente):
    """
    Guarda estrutural. Aceitar o tenant pela URL é confiar no cliente para dizer
    quem ele é — e basta esquecer a checagem numa rota nova para virar vazamento.
    """
    import api.v1 as v1

    caminhos = [r.path for r in v1.router.routes]
    assert caminhos, "o router ficou vazio"
    for p in caminhos:
        assert "location_id" not in p, f"a rota {p} recebe o tenant pelo caminho"


# ── Recusas ──

@pytest.mark.parametrize("headers", [
    {},
    {"Authorization": "Bearer chave-inventada"},
    {"Authorization": "Basic abc"},
    {"X-API-Key": "mc_live_nao_existe"},
])
def test_sem_chave_valida_e_401(ambiente, headers):
    assert ambiente.c.get("/api/v1/me", headers=headers).status_code == 401


def test_chave_revogada_para_de_funcionar(ambiente):
    r = _criar_chave(ambiente)
    chave = r["chave"]
    assert _get(ambiente, "/api/v1/me", chave).status_code == 200

    kid = ambiente.c.get(f"/admin/agents/{LOC_A}/api-keys",
                         cookies={"admin_session": ambiente.cookie}).json()["chaves"][0]["id"]
    ambiente.c.delete(f"/admin/agents/{LOC_A}/api-keys/{kid}",
                      cookies={"admin_session": ambiente.cookie})

    assert _get(ambiente, "/api/v1/me", chave).status_code == 401


def test_revogar_nao_apaga_o_registro(ambiente):
    """Apagar destruiria a resposta para 'quem tinha acesso e até quando'."""
    _criar_chave(ambiente)
    kid = ambiente.c.get(f"/admin/agents/{LOC_A}/api-keys",
                         cookies={"admin_session": ambiente.cookie}).json()["chaves"][0]["id"]
    ambiente.c.delete(f"/admin/agents/{LOC_A}/api-keys/{kid}",
                      cookies={"admin_session": ambiente.cookie})

    linha = ambiente.c.get(f"/admin/agents/{LOC_A}/api-keys",
                           cookies={"admin_session": ambiente.cookie}).json()["chaves"][0]
    assert linha["ativa"] is False and linha["revogada_em"]


def test_recusa_nao_diz_QUAL_foi_o_motivo(ambiente):
    """
    Inexistente, revogada e tenant inativo respondem igual. A diferença entre elas
    só interessa a quem está tentando adivinhar.
    """
    r = _criar_chave(ambiente)
    kid = ambiente.c.get(f"/admin/agents/{LOC_A}/api-keys",
                         cookies={"admin_session": ambiente.cookie}).json()["chaves"][0]["id"]
    ambiente.c.delete(f"/admin/agents/{LOC_A}/api-keys/{kid}",
                      cookies={"admin_session": ambiente.cookie})

    revogada = _get(ambiente, "/api/v1/me", r["chave"])
    inexistente = _get(ambiente, "/api/v1/me", "mc_live_nunca_existiu")
    assert revogada.status_code == inexistente.status_code == 401
    assert revogada.json()["detail"] == inexistente.json()["detail"]


def test_revogar_chave_de_OUTRO_tenant_nao_funciona(ambiente):
    """Id sequencial e adivinhável: o dono tem que entrar no filtro."""
    _criar_chave(ambiente, LOC_A)
    chave_b = _criar_chave(ambiente, LOC_B)["chave"]
    kid_b = ambiente.c.get(f"/admin/agents/{LOC_B}/api-keys",
                           cookies={"admin_session": ambiente.cookie}).json()["chaves"][0]["id"]

    r = ambiente.c.delete(f"/admin/agents/{LOC_A}/api-keys/{kid_b}",
                          cookies={"admin_session": ambiente.cookie}).json()
    assert r["success"] is False
    assert _get(ambiente, "/api/v1/me", chave_b).status_code == 200, "revogou a chave alheia"


# ── O comando de ligar/desligar, ponta a ponta ──

def test_o_CRM_pausa_e_religa_a_IA(ambiente):
    from services import ai_gate

    chave = _criar_chave(ambiente)["chave"]

    r = _post(ambiente, "/api/v1/conversations/5547/ai", chave,
              {"enabled": False, "channel": "whatsapp", "minutos": 120})
    assert r.json()["success"] is True

    estado = _get(ambiente, "/api/v1/conversations/5547/ai?channel=whatsapp", chave).json()
    assert estado["enabled"] is False
    assert estado["motivo"] == ai_gate.CRM
    assert estado["until"], "pausa com prazo pedido veio sem prazo"

    r = _post(ambiente, "/api/v1/conversations/5547/ai", chave, {"enabled": True})
    assert r.json() == {"success": True, "enabled": True, "canais": 1}

    assert _get(ambiente, "/api/v1/conversations/5547/ai?channel=whatsapp",
                chave).json()["enabled"] is True


def test_conversa_nunca_tocada_responde_ligada(ambiente):
    """Ausência de linha é 'ninguém pausou', não 'pausado' — nem 404."""
    chave = _criar_chave(ambiente)["chave"]
    r = _get(ambiente, "/api/v1/conversations/9999/ai?channel=whatsapp", chave)
    assert r.status_code == 200 and r.json()["enabled"] is True


def test_pausar_sem_canal_e_recusado_em_vez_de_chutado(ambiente):
    chave = _criar_chave(ambiente)["chave"]
    r = _post(ambiente, "/api/v1/conversations/5547/ai", chave, {"enabled": False})
    assert r.json()["success"] is False
    assert "channel" in r.json()["error"]


def test_canal_invalido_e_recusado(ambiente):
    chave = _criar_chave(ambiente)["chave"]
    r = _post(ambiente, "/api/v1/conversations/5547/ai", chave,
              {"enabled": False, "channel": "pombo-correio"})
    assert r.json()["success"] is False


def test_minutos_nao_numerico_nao_explode(ambiente):
    chave = _criar_chave(ambiente)["chave"]
    r = _post(ambiente, "/api/v1/conversations/5547/ai", chave,
              {"enabled": False, "channel": "whatsapp", "minutos": "muitos"})
    assert r.status_code == 200 and r.json()["success"] is False


# ── O limitador não guarda a chave ──

def test_o_rate_limit_nao_armazena_a_chave_em_claro(ambiente):
    from services.tenant_auth import chave_do_rate_limit

    chave = "mc_live_segredo_do_cliente"
    req = SimpleNamespace(headers={"authorization": f"Bearer {chave}"},
                          client=SimpleNamespace(host="1.2.3.4"))
    valor = chave_do_rate_limit(req)
    assert chave not in valor
    assert valor.startswith("k:")


# ── O freio de quem não autenticou ──

def test_tentativa_invalida_em_massa_leva_429(ambiente, monkeypatch):
    """
    REGRESSÃO que a revisão de segurança pegou: o `@limiter.limit` envolve a
    FUNÇÃO da rota, e as dependências do FastAPI rodam ANTES dela. Chave inválida
    levantava 401 na dependência e o limiter nunca contava — medido, 300
    tentativas e zero 429.

    O risco não é adivinhar a chave (256 bits), é cada tentativa custar uma query
    e esvaziar o pool de conexões de graça.
    """
    import services.tenant_auth as ta

    ta._falhas.clear()
    codigos = [
        ambiente.c.get("/api/v1/me",
                       headers={"Authorization": f"Bearer mc_live_errada_{i}"}).status_code
        for i in range(ta._MAX_FALHAS + 5)
    ]
    assert codigos[0] == 401
    assert 429 in codigos, "brute force ilimitado: nenhuma tentativa foi freada"
    ta._falhas.clear()


def test_chave_BOA_nao_acumula_falha(ambiente):
    """Contamos só falhas — senão um cliente legítimo de alto volume se auto-bloqueia."""
    import services.tenant_auth as ta

    ta._falhas.clear()
    chave = _criar_chave(ambiente)["chave"]
    for _ in range(ta._MAX_FALHAS + 5):
        assert _get(ambiente, "/api/v1/me", chave).status_code == 200
    ta._falhas.clear()


def test_o_balde_de_falhas_tem_teto_de_memoria(ambiente):
    """IPs aleatórios de um scan não podem virar vazamento lento de memória."""
    import services.tenant_auth as ta

    ta._falhas.clear()
    for i in range(ta._CAP_BALDES + 50):
        ta._registrar_falha(f"origem-{i}")
    assert len(ta._falhas) <= ta._CAP_BALDES
    ta._falhas.clear()


# ── /health não entrega o mapa ──

def test_health_publico_NAO_devolve_location_id(ambiente):
    """
    O `location_id` é o caminho do webhook de entrada
    (`/webhook/waha/{location_id}`). Entregá-lo sem autenticação dá a quem varre a
    internet o mapa completo — e a única coisa entre o mapa e injetar mensagem na
    IA de um cliente é a assinatura do webhook, que é opt-in.
    """
    r = ambiente.c.get("/health")
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["status"] == "healthy"
    assert "tenants" not in corpo, "voltou a publicar a lista de tenants"
    assert LOC_A not in str(corpo) and LOC_B not in str(corpo)
    assert "Cliente A" not in str(corpo)


def test_o_detalhe_continua_disponivel_para_o_OPERADOR(ambiente):
    """Tirar do público não pode ser tirar de quem precisa."""
    r = ambiente.c.get("/admin/health", cookies={"admin_session": ambiente.cookie})
    assert r.status_code == 200
    locs = {t["location_id"] for t in r.json()["tenants"]}
    assert {LOC_A, LOC_B} <= locs


def test_o_detalhe_do_health_exige_login(ambiente):
    assert ambiente.c.get("/admin/health").status_code == 401


def test_variar_a_chave_NAO_da_balde_novo(ambiente):
    """
    O erro que o freio quase teve: balizar pela chave apresentada. Ela é escolhida
    por quem ataca — um caractere diferente por tentativa e o balde nunca fecha.
    Por isso a falha conta por IP.
    """
    import services.tenant_auth as ta

    ta._falhas.clear()
    for i in range(ta._MAX_FALHAS + 5):
        ambiente.c.get("/api/v1/me",
                       headers={"Authorization": f"Bearer mc_live_variando_{i}"})
    assert len(ta._falhas) == 1, (
        f"{len(ta._falhas)} baldes para o mesmo atacante — o freio está indexado "
        "por valor que ele controla"
    )
    ta._falhas.clear()
