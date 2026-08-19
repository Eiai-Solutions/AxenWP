"""
Escolher a voz do Fish Audio por ID, e enxergar o acervo público.

Até 2026-08-19 a tela só tinha um `<select>` alimentado por `GET /model?self=true`
— ou seja, apenas as vozes que a própria conta treinou. Quem nunca treinou nada
via "0 vozes carregadas" e não tinha por onde escolher: nem o acervo público
aparecia, nem havia campo para colar o id de um modelo.

E o runtime sempre aceitou qualquer id — ele vai direto como `reference_id` para a
API do Fish (`services/audio_handler.py`), e a coluna é `String(100)` livre. Só a
tela restringia.

Os endpoints e parâmetros aqui vêm do OpenAPI da própria Fish
(https://api.fish.audio/openapi.json): `GET /model` aceita `self`, `title`,
`language`, `sort_by`; `GET /model/{id}` responde 200/403/404.
"""

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import services.admin_auth as auth
    from data.models import AdminUser, Base
    from utils.config import settings
    from utils.limiter import limiter

    engine = create_engine(f"sqlite:///{tmp_path}/fish.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(auth, "SessionLocal", Session, raising=True)
    monkeypatch.setattr(settings, "debug", True, raising=False)
    monkeypatch.setattr(limiter, "enabled", False, raising=False)

    db = Session()
    db.add(AdminUser(username="op", password_hash=auth.hash_password("s3nha-de-teste"),
                     is_active=True, role=auth.OPERADOR))
    db.commit()
    u = db.query(AdminUser).filter_by(username="op").first()
    cookie = auth.make_session_value(u.username, u.password_hash)
    db.close()

    import main
    c = TestClient(main.app, raise_server_exceptions=False)
    c.cookies.set("admin_session", cookie)
    return c


class _Resposta:
    def __init__(self, status, payload=None, texto=""):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = texto or json.dumps(self._payload)

    def json(self):
        return self._payload


def _fish_falso(monkeypatch, resposta, registro=None):
    """Troca o httpx de admin.ai_agent e registra a URL + params de cada GET."""
    import admin.ai_agent as aa

    class _AC:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, params=None):
            if registro is not None:
                registro.append({"url": url, "headers": headers or {}, "params": params or {}})
            return resposta(url) if callable(resposta) else resposta

    monkeypatch.setattr(aa.httpx, "AsyncClient", _AC, raising=True)


MODELO = {
    "_id": "d13f84b987ad4f22b56d2b47f4eb838e",
    "title": "Ellen PT-BR",
    "languages": ["pt"],
    "state": "trained",
    "author": {"nickname": "Eiai"},
}
# ID REAL da API do Fish (medido 2026-08-19): 32 hex, não 24. A primeira versão
# deste arquivo usava um id inventado de 24 e por isso aprovou uma validação que
# teria recusado todo id de verdade.
ID_VALIDO = "90e65eaaf50e4470b8e6d43ee6afd7d5"
ID_INEXISTENTE = "ffffffffffffffffffffffffffffffff"


# ── colar o ID: o que não existia ──────────────────────────────────────────

def test_id_valido_devolve_o_NOME_da_voz(cliente, monkeypatch):
    """Confirmar antes de salvar é o ponto: id errado só apareceria como agente mudo."""
    _fish_falso(monkeypatch, _Resposta(200, dict(MODELO, _id=ID_VALIDO)))
    r = cliente.post("/admin/agents/fishaudio/model",
                     json={"api_key": "k" * 32, "model_id": ID_VALIDO})
    assert r.status_code == 200, r.text
    v = r.json()["voice"]
    assert v["voice_id"] == ID_VALIDO
    assert v["name"] == "Ellen PT-BR"
    assert v["languages"] == ["pt"]


def test_o_formato_ACEITO_e_o_que_a_api_realmente_devolve(cliente, monkeypatch):
    """
    REGRESSÃO de um defeito meu, 2026-08-19.

    A primeira versão desta rota exigia `[0-9a-f]{24}` — eu supus ObjectId do
    Mongo e escrevi a regra sem medir. Os testes concordaram porque o id falso
    deles tinha 24 caracteres: dado inventado a partir da mesma suposição não
    checa nada, só a repete.

    Medido depois contra a API: **60 de 60 modelos têm 32 hex**. A validação
    recusaria todo id de verdade, no campo criado justamente para colar id.

    Estes são ids reais colhidos da API. Se alguém reapertar a faixa, quebra aqui.
    """
    reais = [
        "90e65eaaf50e4470b8e6d43ee6afd7d5",
        "d13f84b987ad4f22b56d2b47f4eb838e",
        "2368250f3e6c46f691956f0523425b72",
    ]
    for mid in reais:
        assert len(mid) == 32, "amostra do teste deixou de refletir a API"
        _fish_falso(monkeypatch, _Resposta(200, dict(MODELO, _id=mid)))
        r = cliente.post("/admin/agents/fishaudio/model",
                         json={"api_key": "k" * 32, "model_id": mid})
        assert r.status_code == 200, f"recusou um id REAL da Fish: {mid} -> {r.text}"
        assert r.json()["voice"]["voice_id"] == mid


def test_uuid_com_hifen_e_normalizado(cliente, monkeypatch):
    """O id é um UUID sem hífen; copiado de outro lugar pode vir com."""
    com_hifen = "90e65eaa-f50e-4470-b8e6-d43ee6afd7d5"
    sem = com_hifen.replace("-", "")
    _fish_falso(monkeypatch, _Resposta(200, dict(MODELO, _id=sem)))
    r = cliente.post("/admin/agents/fishaudio/model",
                     json={"api_key": "k" * 32, "model_id": com_hifen})
    assert r.status_code == 200, r.text
    assert r.json()["voice"]["voice_id"] == sem


def test_id_com_formato_errado_nem_chega_a_chamar_o_fish(cliente, monkeypatch):
    """
    Mandar lixo para fora leva a chave junto e devolve o erro genérico deles.
    Barrar no formato dá mensagem útil e não gasta a chamada.
    """
    chamadas = []
    _fish_falso(monkeypatch, _Resposta(200, MODELO), chamadas)
    r = cliente.post("/admin/agents/fishaudio/model",
                     json={"api_key": "k" * 32, "model_id": "voz da ellen"})
    assert r.status_code == 400
    assert "hexadecimal" in r.json()["detail"].lower()
    assert chamadas == [], "mandou entrada obviamente inválida para o Fish Audio"


def test_id_inexistente_e_id_sem_permissao_dao_mensagens_DIFERENTES(cliente, monkeypatch):
    """
    Dizer "erro" para os dois faz o operador conferir a chave quando o problema é o
    id — e o contrário. A API distingue; a tela também tem que distinguir.
    """
    _fish_falso(monkeypatch, _Resposta(404, {}, "not found"))
    r404 = cliente.post("/admin/agents/fishaudio/model",
                        json={"api_key": "k" * 32, "model_id": ID_VALIDO})

    _fish_falso(monkeypatch, _Resposta(403, {}, "forbidden"))
    r403 = cliente.post("/admin/agents/fishaudio/model",
                        json={"api_key": "k" * 32, "model_id": ID_VALIDO})

    assert r404.status_code == 404 and r403.status_code == 403
    assert r404.json()["detail"] != r403.json()["detail"]
    assert "ID" in r404.json()["detail"]
    assert "chave" in r403.json()["detail"].lower()


def test_sem_chave_nao_consulta_nada(cliente, monkeypatch):
    chamadas = []
    _fish_falso(monkeypatch, _Resposta(200, MODELO), chamadas)
    r = cliente.post("/admin/agents/fishaudio/model", json={"api_key": "", "model_id": ID_VALIDO})
    assert r.status_code == 400 and chamadas == []


def test_a_chave_vai_no_CORPO_e_nunca_na_url(cliente, monkeypatch):
    """
    Em query string a chave entra no log de acesso do proxy e no histórico do
    navegador. Este teste trava o método: virar GET reintroduz o vazamento.
    """
    _fish_falso(monkeypatch, _Resposta(200, dict(MODELO, _id=ID_VALIDO)))
    assert cliente.get(f"/admin/agents/fishaudio/model?api_key=k&model_id={ID_VALIDO}").status_code == 405


def test_resposta_200_sem_id_nao_inventa_o_id_pedido(cliente, monkeypatch):
    """Se o formato da API mudar, gravar um id nunca confirmado é pior que falhar."""
    _fish_falso(monkeypatch, _Resposta(200, {"title": "Sem id"}))
    r = cliente.post("/admin/agents/fishaudio/model",
                     json={"api_key": "k" * 32, "model_id": ID_VALIDO})
    assert r.status_code == 200
    assert r.json()["voice"]["voice_id"] == ID_VALIDO


# ── listar: o acervo público, que era invisível ────────────────────────────

def _lista(itens):
    return _Resposta(200, {"items": itens, "total": len(itens)})


def test_escopo_minhas_manda_self_true(cliente, monkeypatch):
    chamadas = []
    _fish_falso(monkeypatch, _lista([MODELO]), chamadas)
    r = cliente.get("/admin/agents/fishaudio/voices", params={"api_key": "k" * 32})
    assert r.status_code == 200
    assert chamadas[0]["params"].get("self") == "true"


def test_escopo_publicas_NAO_manda_self(cliente, monkeypatch):
    """
    Era o defeito: `self=true` fixo escondia o acervo público, que é onde estão as
    vozes que a maioria usa. A API já tem `self=false` como default.
    """
    chamadas = []
    _fish_falso(monkeypatch, _lista([MODELO]), chamadas)
    r = cliente.get("/admin/agents/fishaudio/voices",
                    params={"api_key": "k" * 32, "escopo": "publicas"})
    assert r.status_code == 200
    assert "self" not in chamadas[0]["params"], "continuou filtrando só as vozes da conta"


def test_busca_e_idioma_chegam_na_api_com_os_nomes_DELA(cliente, monkeypatch):
    """`title` e `language` são os nomes do OpenAPI da Fish — errar aqui é filtro mudo."""
    chamadas = []
    _fish_falso(monkeypatch, _lista([]), chamadas)
    cliente.get("/admin/agents/fishaudio/voices",
                params={"api_key": "k" * 32, "escopo": "publicas",
                        "busca": "  narrador  ", "idioma": "pt"})
    p = chamadas[0]["params"]
    assert p["title"] == "narrador", "não passou a busca (ou não tirou o espaço)"
    assert p["language"] == "pt"


def test_filtro_vazio_nao_vira_parametro(cliente, monkeypatch):
    """`title=""` filtraria por título vazio e voltaria nada."""
    chamadas = []
    _fish_falso(monkeypatch, _lista([MODELO]), chamadas)
    cliente.get("/admin/agents/fishaudio/voices", params={"api_key": "k" * 32, "busca": "   "})
    assert "title" not in chamadas[0]["params"]


def test_lista_vazia_e_sucesso_e_nao_erro(cliente, monkeypatch):
    """
    Zero vozes é um estado normal (conta sem modelo treinado), não falha. Tratar
    como erro manda o operador conferir a chave que está certa — foi o que
    aconteceu na prática.
    """
    _fish_falso(monkeypatch, _lista([]))
    r = cliente.get("/admin/agents/fishaudio/voices", params={"api_key": "k" * 32})
    assert r.status_code == 200
    assert r.json()["success"] is True and r.json()["voices"] == []


def test_rotas_exigem_login(tmp_path, monkeypatch):
    """As duas mandam a chave do tenant para fora; sem sessão, nem começam."""
    from utils.config import settings
    from utils.limiter import limiter

    monkeypatch.setattr(settings, "debug", True, raising=False)
    monkeypatch.setattr(limiter, "enabled", False, raising=False)
    import main

    anon = TestClient(main.app, raise_server_exceptions=False)
    assert anon.get("/admin/agents/fishaudio/voices", params={"api_key": "k"}).status_code in (401, 403, 303, 307)
    assert anon.post("/admin/agents/fishaudio/model",
                     json={"api_key": "k", "model_id": ID_VALIDO}).status_code in (401, 403, 303, 307)
