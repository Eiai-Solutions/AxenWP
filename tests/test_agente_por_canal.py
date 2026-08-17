"""
Cada canal lê a config do SEU agente.

Estes são bugs LATENTES: com um agente por instância — o caso de hoje em produção
— `.first()` sem filtro de canal acerta por sorte. Ativam no minuto em que o
segundo agente é criado, que é exatamente o que o painel vai passar a permitir.

E ativam em SILÊNCIO. Nenhum dos dois gera erro:

- `_debounce_seconds` pegava o tempo de um agente qualquer: bastava existir um
  agente de Telegram para o WhatsApp passar a usar a janela dele;
- `ai_is_enabled` (modo whatsapp_only) lia `is_active` de um agente qualquer —
  **pausar o agente do Telegram desligava a IA do WhatsApp.**
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from data.models import AIAgent, Base, Tenant

LOC = "wp_dois_canais"


@pytest.fixture
def dois_agentes(tmp_path, monkeypatch):
    """WhatsApp ativo com debounce 3s; Telegram PAUSADO com debounce 30s."""
    import data.database as dbmod
    import services.inbound_pipeline as ip

    engine = create_engine(f"sqlite:///{tmp_path}/canal.db",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    # `inbound_pipeline` importa SessionLocal DENTRO das funcoes, entao o patch
    # tem que ser na origem.
    monkeypatch.setattr(dbmod, "SessionLocal", Session, raising=True)

    db = Session()
    db.add(Tenant(location_id=LOC, company_name="Dois Canais", mode="whatsapp_only"))
    # Telegram entra PRIMEIRO de propósito: assim o `.first()` sem filtro tende a
    # devolvê-lo, e o teste falha de verdade quando a correção sai.
    db.add(AIAgent(location_id=LOC, channel="telegram", name="Bot",
                   prompt="p", is_active=False, debounce_seconds=30))
    db.add(AIAgent(location_id=LOC, channel="whatsapp", name="Sofia",
                   prompt="p", is_active=True, debounce_seconds=3))
    db.commit()
    db.close()
    return SimpleNamespace(Session=Session, ip=ip)


def test_debounce_e_o_do_canal_que_recebeu(dois_agentes):
    ip = dois_agentes.ip

    assert ip._debounce_seconds(LOC, "whatsapp") == 3.0
    assert ip._debounce_seconds(LOC, "telegram") == 30.0


@pytest.mark.asyncio
async def test_pausar_o_telegram_nao_desliga_a_ia_do_whatsapp(dois_agentes):
    """
    O pior dos dois. O agente do WhatsApp está ATIVO; o do Telegram, pausado.
    Sem o filtro de canal, o lead do WhatsApp deixava de ser atendido — sem um
    único erro no log, e sem ninguém relacionar a causa ao Telegram.
    """
    ip = dois_agentes.ip
    tenant = SimpleNamespace(location_id=LOC, mode="whatsapp_only")

    ligada = await ip.ai_is_enabled(tenant, LOC, "5547999", None, "whatsapp")
    assert ligada is True, "pausar o Telegram derrubou a IA do WhatsApp"

    desligada = await ip.ai_is_enabled(tenant, LOC, "5547999", None, "telegram")
    assert desligada is False, "o agente pausado respondeu assim mesmo"


@pytest.mark.asyncio
async def test_canal_sem_agente_nao_atende(dois_agentes):
    """Canal que não tem agente não pode herdar o `is_active` de outro."""
    ip = dois_agentes.ip
    tenant = SimpleNamespace(location_id=LOC, mode="whatsapp_only")

    assert await ip.ai_is_enabled(tenant, LOC, "5547999", None, "instagram") is False


def test_debounce_cai_no_padrao_quando_o_canal_nao_tem_agente(dois_agentes):
    ip = dois_agentes.ip
    # Não herda os 30s do Telegram nem os 3s do WhatsApp.
    assert ip._debounce_seconds(LOC, "instagram") not in (3.0, 30.0)


# ── As telas por instância: determinísticas, não arbitrárias ──

def test_a_tela_escolhe_agente_de_forma_deterministica(dois_agentes):
    """
    Enquanto não há seletor de agente nessas telas, a escolha precisa ao menos ser
    ESTÁVEL — senão o operador vê o campo de qualificação de um agente diferente a
    cada refresh, e não tem como perceber que isso está acontecendo.
    """
    from admin.ai_agent import _agente_da_tela

    db = dois_agentes.Session()
    try:
        escolhas = {_agente_da_tela(db, LOC).id for _ in range(5)}
        assert len(escolhas) == 1, "a tela escolheu agentes diferentes entre chamadas"

        assert _agente_da_tela(db, LOC, "whatsapp").channel == "whatsapp"
        assert _agente_da_tela(db, LOC, "telegram").channel == "telegram"
        assert _agente_da_tela(db, LOC, "instagram") is None
    finally:
        db.close()
