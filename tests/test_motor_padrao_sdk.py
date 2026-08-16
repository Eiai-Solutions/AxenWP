"""
Agente novo nasce no motor SDK (tool-use), não no legado.

Os 6 que existiam foram migrados em 2026-08-16, depois de o motor ser provado
contra os prompts reais deles. Estes testes impedem que um agente criado depois
disso volte silenciosamente para o single-turn — que é o motor que NÃO sabe usar
as ferramentas de qualificar lead e escalar para humano.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from data.models import AIAgent, Base


@pytest.fixture
def db(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/motor.db")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)


def test_agente_criado_sem_dizer_o_motor_nasce_claude(db):
    s = db()
    s.add(AIAgent(location_id="loc1", channel="whatsapp", prompt="oi"))
    s.commit()
    assert s.query(AIAgent).first().agent_engine == "claude"
    s.close()


def test_o_formulario_do_painel_tambem_tem_default_claude():
    """Se o Form voltar para 'langchain', salvar sem tocar no seletor rebaixa o agente."""
    import inspect

    from admin import ai_agent

    padrao = inspect.signature(ai_agent.save_agent_settings).parameters["agent_engine"].default
    assert getattr(padrao, "default", padrao) == "claude"


def test_provisionamento_da_mestre_define_o_motor_explicitamente():
    """
    Explícito, não herdado do default da coluna: as tools de qualificar e escalar
    são justamente o que o motor legado não sabe usar.
    """
    import inspect

    from services import agent_provisioning

    fonte = inspect.getsource(agent_provisioning)
    assert '"agent_engine": "claude"' in fonte


def test_o_modal_oferece_o_motor_sdk_primeiro_e_modelos_atuais():
    from pathlib import Path

    html = Path("web/templates/partials/modals.html").read_text(encoding="utf-8")
    pos_claude = html.index('<option value="claude">')
    pos_legado = html.index('<option value="langchain">')
    assert pos_claude < pos_legado, "o legado aparece antes do motor recomendado"
    assert "claude-opus-4-8" not in html, "id de modelo desatualizado no seletor"
    assert "claude-opus-5" in html
