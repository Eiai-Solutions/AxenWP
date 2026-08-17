"""
Wizard de criação de agente: rascunho incremental e publicação.

As duas propriedades que este arquivo existe para garantir:

1. **Agente pela metade não existe para o runtime.** O rascunho vive em
   `agent_drafts`, fora de `ai_agents`. Publicar é a única operação que cria o
   agente — e só quando `pode_publicar` autoriza, reavaliado no servidor.
2. **Id sequencial não abre porta de outro tenant.** O rascunho é comparado com o
   `location_id` do path em toda operação.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from data.models import AgentDraft, AIAgent, Base, Tenant

LOC_A = "wp_aaaaaaaaaaaa"
LOC_B = "wp_bbbbbbbbbbbb"


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    import services.draft_service as ds
    import services.admin_auth as auth
    from data.models import AdminUser
    from utils.config import settings
    from utils.limiter import limiter

    engine = create_engine(f"sqlite:///{tmp_path}/wiz.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    monkeypatch.setattr(ds, "SessionLocal", Session, raising=True)
    # Chave do motor presente por padrão; o teste que cobre a ausência a desliga.
    monkeypatch.setattr(ds, "_tem_chave_do_motor", lambda: True, raising=True)

    # O CRM real não existe no teste. Mockamos só o CATÁLOGO — o portão de
    # qualificação (que é o que importa aqui) roda de verdade em cima dele.
    import services.agent_provisioning as ap
    async def _sem_crm(*a, **kw):
        return {"ok": False, "error": "CRM indisponível no teste"}
    monkeypatch.setattr(ap, "fetch_crm_catalog", _sem_crm, raising=True)
    monkeypatch.setattr(auth, "SessionLocal", Session, raising=True)
    # As rotas do wizard também abrem sessão própria (SystemSettings, na
    # importação). Sem este patch elas caem no banco real e o teste vira 500.
    import admin.ai_agent as aa
    monkeypatch.setattr(aa, "SessionLocal", Session, raising=True)
    monkeypatch.setattr(settings, "debug", True, raising=False)
    monkeypatch.setattr(limiter, "enabled", False, raising=False)

    db = Session()
    # A tem WhatsApp via WAHA e CRM; B é whatsapp_only.
    db.add(Tenant(location_id=LOC_A, company_name="Cliente A", mode="ghl", pit_token="pit",
                  whatsapp_provider="waha", waha_base_url="https://w", waha_session="s",
                  waha_api_key="k"))
    db.add(Tenant(location_id=LOC_B, company_name="Cliente B", mode="whatsapp_only",
                  whatsapp_provider="waha", waha_base_url="https://w", waha_session="s",
                  waha_api_key="k"))
    db.add(AdminUser(username="op", password_hash=auth.hash_password("senha-operador"),
                     is_active=True, role=auth.OPERADOR))
    db.commit()
    u = db.query(AdminUser).filter_by(username="op").first()
    cookie = auth.make_session_value(u.username, u.password_hash)
    db.close()

    import main
    return SimpleNamespace(
        c=TestClient(main.app, raise_server_exceptions=False), cookie=cookie, Session=Session
    )


def _abrir(a, loc=LOC_A):
    return a.c.post(f"/admin/agents/{loc}/wizard/abrir",
                    cookies={"admin_session": a.cookie}).json()


def _salvar(a, draft_id, mudancas, loc=LOC_A):
    return a.c.post(f"/admin/agents/{loc}/wizard/{draft_id}/etapa",
                    json=mudancas, cookies={"admin_session": a.cookie}).json()


def _publicar(a, draft_id, loc=LOC_A):
    return a.c.post(f"/admin/agents/{loc}/wizard/{draft_id}/publicar",
                    cookies={"admin_session": a.cookie}).json()


# --------------------------------------------------------------------------- #
# Abrir e retomar
# --------------------------------------------------------------------------- #
def test_abrir_devolve_etapas_derivadas_do_tenant(ambiente):
    d = _abrir(ambiente)
    assert d["success"] is True
    ids = [e["id"] for e in d["etapas"]]
    assert ids == ["canal", "identidade", "qualificacao", "revisao"]
    # Um canal só: já vem escolhido, a etapa não pede o óbvio.
    assert d["rascunho"]["channel"] == "whatsapp"


def test_tenant_com_crm_ve_a_etapa_pedindo_funil(ambiente):
    qual = next(e for e in _abrir(ambiente)["etapas"] if e["id"] == "qualificacao")
    assert qual["variante"] == "crm"


def test_tenant_sem_crm_ve_a_MESMA_etapa_em_outra_forma(ambiente):
    qual = next(e for e in _abrir(ambiente, LOC_B)["etapas"] if e["id"] == "qualificacao")
    assert qual["variante"] == "sem_crm"
    assert qual["dados"]["precisa_funil"] is False


def test_clicar_mais_duas_vezes_retoma_o_mesmo_rascunho(ambiente):
    """Dois rascunhos meio preenchidos e a pessoa sem saber qual continuar."""
    primeiro = _abrir(ambiente)["rascunho"]["id"]
    assert _abrir(ambiente)["rascunho"]["id"] == primeiro

    db = ambiente.Session()
    assert db.query(AgentDraft).filter_by(location_id=LOC_A).count() == 1
    db.close()


def test_salvar_e_incremental_e_sobrevive_a_recarga(ambiente):
    """A etapa de identidade pode levar minutos (a entrevista). Refresh não pode perder."""
    d = _abrir(ambiente)["rascunho"]["id"]
    _salvar(ambiente, d, {"origem": "entrevista", "etapa_atual": "identidade"})
    _salvar(ambiente, d, {"prompt": "Você é a Sofia, SDR...", "agent_name": "Sofia"})

    r = ambiente.c.get(f"/admin/agents/{LOC_A}/wizard/{d}",
                       cookies={"admin_session": ambiente.cookie}).json()
    assert r["rascunho"]["prompt"].startswith("Você é a Sofia")
    assert r["rascunho"]["origem"] == "entrevista"
    assert r["rascunho"]["etapa_atual"] == "identidade"


# --------------------------------------------------------------------------- #
# O runtime não pode ver agente pela metade
# --------------------------------------------------------------------------- #
def test_rascunho_NAO_cria_agente_ate_publicar(ambiente):
    d = _abrir(ambiente)["rascunho"]["id"]
    _salvar(ambiente, d, {"prompt": "Você é a Sofia...", "agent_name": "Sofia"})

    db = ambiente.Session()
    assert db.query(AIAgent).count() == 0, "agente pela metade apareceu em ai_agents"
    db.close()


def test_publicar_cria_o_agente_ligado_e_no_motor_sdk(ambiente):
    d = _abrir(ambiente)["rascunho"]["id"]
    _salvar(ambiente, d, {"prompt": "Você é a Sofia, SDR da empresa.", "agent_name": "Sofia",
                          "qualificar": False})
    r = _publicar(ambiente, d)

    assert r["success"] is True and r["criado"] is True

    db = ambiente.Session()
    a = db.query(AIAgent).filter_by(location_id=LOC_A).first()
    assert a is not None
    assert a.prompt.startswith("Você é a Sofia")
    assert a.agent_engine == "claude"
    assert a.is_active is True
    assert db.query(AgentDraft).filter_by(id=d).first().status == "publicado"
    db.close()


def test_nao_publica_sem_prompt_mesmo_se_a_tela_mandar(ambiente):
    """O guard é reavaliado no servidor: a tela pode estar desatualizada."""
    d = _abrir(ambiente)["rascunho"]["id"]
    r = _publicar(ambiente, d)

    assert r["success"] is False
    assert "prompt" in r["error"].lower()

    db = ambiente.Session()
    assert db.query(AIAgent).count() == 0
    db.close()


def test_publicar_duas_vezes_nao_duplica(ambiente):
    d = _abrir(ambiente)["rascunho"]["id"]
    _salvar(ambiente, d, {"prompt": "Você é a Sofia.", "agent_name": "Sofia"})
    assert _publicar(ambiente, d)["success"] is True
    assert _publicar(ambiente, d)["success"] is False

    db = ambiente.Session()
    assert db.query(AIAgent).count() == 1
    db.close()


def test_publicar_sobre_agente_existente_ATUALIZA_em_vez_de_duplicar(ambiente):
    db = ambiente.Session()
    db.add(AIAgent(location_id=LOC_A, channel="whatsapp", prompt="antigo", name="Velho"))
    db.commit()
    db.close()

    d = _abrir(ambiente)["rascunho"]["id"]
    _salvar(ambiente, d, {"prompt": "prompt novo do wizard", "agent_name": "Sofia"})
    r = _publicar(ambiente, d)
    assert r["criado"] is False

    db = ambiente.Session()
    agentes = db.query(AIAgent).filter_by(location_id=LOC_A).all()
    assert len(agentes) == 1
    assert agentes[0].prompt == "prompt novo do wizard"
    db.close()


# --------------------------------------------------------------------------- #
# Escopo: id sequencial não abre porta de outro tenant
# --------------------------------------------------------------------------- #
def test_rascunho_de_um_tenant_nao_e_acessivel_pelo_outro(ambiente):
    d = _abrir(ambiente, LOC_A)["rascunho"]["id"]

    lido = ambiente.c.get(f"/admin/agents/{LOC_B}/wizard/{d}",
                          cookies={"admin_session": ambiente.cookie}).json()
    assert lido["success"] is False

    alterado = _salvar(ambiente, d, {"prompt": "invadido"}, loc=LOC_B)
    assert alterado["success"] is False

    publicado = _publicar(ambiente, d, loc=LOC_B)
    assert publicado["success"] is False

    db = ambiente.Session()
    assert db.query(AgentDraft).filter_by(id=d).first().prompt is None
    assert db.query(AIAgent).count() == 0
    db.close()


def test_rotas_do_wizard_exigem_operador(ambiente):
    """O router já nasce com require_admin; isto trava que continue assim."""
    for metodo, url in [
        ("POST", f"/admin/agents/{LOC_A}/wizard/abrir"),
        ("GET", f"/admin/agents/{LOC_A}/wizard/1"),
        ("POST", f"/admin/agents/{LOC_A}/wizard/1/publicar"),
    ]:
        r = ambiente.c.request(metodo, url)
        assert r.status_code in (401, 403), f"{metodo} {url} respondeu {r.status_code} sem sessão"


@pytest.mark.parametrize("ruim", ["loc;drop", "a/b", "x" * 60, "ab"])
def test_location_id_malformado_e_recusado_antes_do_banco(ambiente, ruim):
    """
    A regra real é  — hífen É aceito. Testamos o que ela
    de fato rejeita: separador de statement, barra, comprimento fora da faixa.
    """
    r = ambiente.c.post(f"/admin/agents/{ruim}/wizard/abrir",
                        cookies={"admin_session": ambiente.cookie})
    corpo = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    assert r.status_code == 404 or corpo.get("success") is False


# --------------------------------------------------------------------------- #
# Substituir agente de produção: avisa antes e guarda a versão anterior
# --------------------------------------------------------------------------- #
def test_o_estado_avisa_que_vai_substituir_agente_existente(ambiente):
    """
    Substituir é legítimo; fazer sem saber não é. A Joorney tem 22k caracteres de
    prompt em produção — descobrir depois de clicar é a pior hora.
    """
    db = ambiente.Session()
    db.add(AIAgent(location_id=LOC_A, channel="whatsapp",
                   prompt="P" * 22000, name="Sofia", is_active=True))
    db.commit()
    db.close()

    d = _abrir(ambiente)
    aviso = d["vai_substituir"]
    assert aviso is not None, "publicaria por cima sem avisar"
    assert aviso["nome"] == "Sofia"
    assert aviso["tamanho_prompt"] == 22000
    assert aviso["ativo"] is True


def test_sem_agente_no_canal_nao_ha_aviso(ambiente):
    assert _abrir(ambiente)["vai_substituir"] is None


def test_publicar_por_cima_GUARDA_a_versao_anterior_no_historico(ambiente):
    """
    CLAUDE.md: snapshot sempre que `agent.prompt` é escrito. Aqui vale dobrado —
    sem ele, um wizard concluído por engano apagaria 22k caracteres sem volta.
    """
    from data.models import AgentPromptHistory

    db = ambiente.Session()
    db.add(AIAgent(location_id=LOC_A, channel="whatsapp",
                   prompt="prompt antigo de producao", name="Sofia", is_active=True))
    db.commit()
    db.close()

    d = _abrir(ambiente)["rascunho"]["id"]
    _salvar(ambiente, d, {"prompt": "prompt novo", "agent_name": "Sofia 2"})
    assert _publicar(ambiente, d)["success"] is True

    db = ambiente.Session()
    versoes = db.query(AgentPromptHistory).filter_by(location_id=LOC_A).all()
    fontes = {v.source for v in versoes}
    assert "wizard_overwrite" in fontes, "versao anterior nao foi guardada"
    assert any(v.prompt == "prompt antigo de producao" for v in versoes)
    db.close()


def test_sem_chave_anthropic_nao_publica_agente_mudo(ambiente, monkeypatch):
    """
    O agente nasce no motor `claude`. Sem chave, `ai_service` cai em engine=None e
    ele não responde — o operador acha que criou e o lead fica no vácuo. Melhor
    impedir com motivo do que publicar um agente mudo.
    """
    import services.draft_service as ds
    monkeypatch.setattr(ds, "_tem_chave_do_motor", lambda: False, raising=True)

    d = _abrir(ambiente)["rascunho"]["id"]
    _salvar(ambiente, d, {"prompt": "Você é a Sofia.", "agent_name": "Sofia"})
    r = _publicar(ambiente, d)

    assert r["success"] is False
    assert "anthropic" in r["error"].lower()

    db = ambiente.Session()
    assert db.query(AIAgent).count() == 0
    db.close()


def test_publicar_por_cima_muda_updated_at_e_invalida_o_cache(ambiente):
    """
    O cache do ai_service é chaveado por `agent.updated_at`. Se ele não mudasse,
    o agente atualizado só valeria depois de reiniciar o processo.
    """
    db = ambiente.Session()
    db.add(AIAgent(location_id=LOC_A, channel="whatsapp", prompt="antigo", name="Sofia"))
    db.commit()
    antes = db.query(AIAgent).filter_by(location_id=LOC_A).first().updated_at
    db.close()

    d = _abrir(ambiente)["rascunho"]["id"]
    _salvar(ambiente, d, {"prompt": "prompt novo publicado pelo wizard"})
    assert _publicar(ambiente, d)["success"] is True

    db = ambiente.Session()
    depois = db.query(AIAgent).filter_by(location_id=LOC_A).first().updated_at
    db.close()
    assert depois != antes, "updated_at nao mudou — o cache serviria o agente velho"


# --------------------------------------------------------------------------- #
# Achados da revisão adversarial — cada um com o dano que produzia
# --------------------------------------------------------------------------- #
def test_qualificacao_NAO_liga_sem_funil_em_tenant_com_crm(ambiente):
    """
    O publish copiava o JSON do rascunho e contornava o portão fail-closed do
    `agent_provisioning`. Com CRM e sem funil, o agente diria ao lead que
    registrou, o handler pularia o CRM, o QualifiedLead seria gravado e a IA
    ficaria pausada naquele lead PARA SEMPRE — sem conserto, pela idempotência.
    """
    d = _abrir(ambiente)["rascunho"]["id"]
    _salvar(ambiente, d, {
        "prompt": "Você é a Sofia.", "qualificar": True,
        "qualification_fields": [{"label": "Qual seu orçamento?"}],
    })
    assert _publicar(ambiente, d)["success"] is True

    db = ambiente.Session()
    a = db.query(AIAgent).filter_by(location_id=LOC_A).first()
    assert a.qualification_enabled is False, "ligou qualificacao sem funil em tenant com CRM"
    db.close()


def test_publicar_por_cima_NAO_religa_agente_pausado(ambiente):
    """Agente pausado durante um incidente não pode voltar a atender por um publish."""
    db = ambiente.Session()
    db.add(AIAgent(location_id=LOC_A, channel="whatsapp", prompt="prod",
                   name="Sofia", is_active=False))
    db.commit()
    db.close()

    d = _abrir(ambiente)["rascunho"]["id"]
    _salvar(ambiente, d, {"prompt": "prompt novo"})
    assert _publicar(ambiente, d)["success"] is True

    db = ambiente.Session()
    assert db.query(AIAgent).filter_by(location_id=LOC_A).first().is_active is False
    db.close()


def test_publicar_num_canal_ALIAS_desfaz_o_alias(ambiente):
    """
    Sem isto o registro era gravado, a rota dizia sucesso, e o runtime continuava
    servindo o agente do canal apontado — o operador testava e recebia a persona
    errada, sem um erro sequer no log.
    """
    db = ambiente.Session()
    db.add(AIAgent(location_id=LOC_A, channel="whatsapp",
                   prompt="(alias)", linked_to_channel="telegram"))
    db.commit()
    db.close()

    d = _abrir(ambiente)["rascunho"]["id"]
    _salvar(ambiente, d, {"prompt": "prompt proprio deste canal"})
    assert _publicar(ambiente, d)["success"] is True

    db = ambiente.Session()
    a = db.query(AIAgent).filter_by(location_id=LOC_A, channel="whatsapp").first()
    assert a.linked_to_channel is None, "continuaria espelhando o outro canal"
    db.close()


def test_nao_publica_em_canal_que_o_tenant_nao_tem(ambiente):
    """Publicava em canal sem transporte: painel verde, nenhuma mensagem chegando."""
    d = _abrir(ambiente)["rascunho"]["id"]
    _salvar(ambiente, d, {"channel": "telegram", "prompt": "Você é a Sofia."})
    r = _publicar(ambiente, d)

    assert r["success"] is False
    assert "telegram" in r["error"].lower()

    db = ambiente.Session()
    assert db.query(AIAgent).count() == 0
    db.close()


def test_campos_de_qualificacao_com_forma_errada_sao_RECUSADOS(ambiente):
    """
    Lista de strings (forma plausível para uma tela) quebrava `prompt_builder` no
    caminho quente de TODA mensagem daquele tenant — e não há retry no projeto,
    então a mensagem do lead se perdia.
    """
    d = _abrir(ambiente)["rascunho"]["id"]
    r = _salvar(ambiente, d, {"qualification_fields": ["orcamento", "prazo"]})
    assert r["success"] is False

    r2 = _salvar(ambiente, d, {"prompt": {"nao": "e texto"}})
    assert r2["success"] is False

    db = ambiente.Session()
    assert db.query(AgentDraft).filter_by(id=d).first().qualification_fields is None
    db.close()


def test_o_aviso_mostra_o_que_realmente_muda(ambiente):
    """Antes só dizia nome/tamanho/ativo — escondia funil e alias."""
    db = ambiente.Session()
    db.add(AIAgent(location_id=LOC_A, channel="whatsapp", prompt="p", name="Sofia",
                   qualification_enabled=True, qualification_pipeline_id="pipe-real"))
    db.commit()
    db.close()

    aviso = _abrir(ambiente)["vai_substituir"]
    assert aviso["qualificacao_ligada"] is True
    assert aviso["tem_funil"] is True
    assert "e_alias_de" in aviso


def test_rascunho_de_um_operador_nao_vaza_para_o_outro(ambiente):
    """
    O painel é da equipe. Sem dono, o "+" de um devolvia o rascunho meio
    preenchido do outro, e salvar era last-write-wins silencioso.
    """
    import services.draft_service as ds

    primeiro = ds._abrir_sync(LOC_A, "operador_a")
    ds._salvar_sync(primeiro, LOC_A, {"prompt": "trabalho do A"})

    segundo = ds._abrir_sync(LOC_A, "operador_b")
    assert segundo != primeiro, "B recebeu o rascunho do A"

    db = ambiente.Session()
    assert db.query(AgentDraft).filter_by(id=segundo).first().prompt is None
    assert db.query(AgentDraft).filter_by(id=primeiro).first().prompt == "trabalho do A"
    db.close()


def test_pode_publicar_falha_FECHADO_sem_etapas():
    """Lista vazia = tenant não resolvido. Antes o guard de canal sumia junto."""
    from services.agent_wizard import pode_publicar

    ok, motivo = pode_publicar({"channel": "whatsapp", "prompt": "x" * 40}, [])
    assert ok is False and motivo


# --------------------------------------------------------------------------- #
# O ciclo das portas: o que o cliente respondeu precisa VOLTAR para o rascunho
# --------------------------------------------------------------------------- #
def _importar(a, draft_id, loc=LOC_A):
    return a.c.post(f"/admin/agents/{loc}/wizard/{draft_id}/importar",
                    cookies={"admin_session": a.cookie}).json()


def _submissao(a, loc=LOC_A, **campos):
    from data.models import OnboardingSubmission
    db = a.Session()
    dados = {"company_name": "Padaria Aurora", "products_services": "paes e bolos",
             "agent_goal": "tirar duvida e anotar pedido", **campos}
    s = OnboardingSubmission(tenant_location_id=loc, form_data=dados, status="pending")
    db.add(s)
    db.commit()
    db.refresh(s)
    sid = s.id
    db.close()
    return sid


@pytest.fixture
def mestre_falsa(monkeypatch):
    """A Mestre de verdade custa dinheiro; o que testamos aqui é o encanamento."""
    import admin.ai_agent as aa

    async def _fake(settings, form_data):
        form_data["agent_name"] = "Sofia"
        return (
            f"Voce e a Sofia, atendente da {form_data.get('company_name')}.",
            [{"label": "Qual o seu bairro?", "description": "para a entrega", "type": "text"}],
        )

    monkeypatch.setattr(aa, "_run_master", _fake, raising=True)
    return _fake


def test_a_porta_recomendada_fecha_o_ciclo(ambiente, mestre_falsa):
    """
    REGRESSÃO — a porta marcada como RECOMENDADA não tinha como terminar.

    `wizardPorta` gravava só `{origem}` e abria uma aba. A entrevista concluía e
    criava a submissão, mas nada voltava: `prompt` ficava None e `pode_publicar`
    reprovava para sempre. As colunas `submission_id`/`spec` já existiam e já eram
    lidas no publish — consumidor sem produtor.
    """
    sid = _submissao(ambiente)
    d = _abrir(ambiente)["rascunho"]["id"]
    _salvar(ambiente, d, {"origem": "entrevista", "etapa_atual": "identidade"})

    assert _abrir(ambiente)["rascunho"]["prompt"] is None
    assert _abrir(ambiente)["pode_publicar"] is False

    r = _importar(ambiente, d)

    assert r["success"] is True, r
    assert r["empresa"] == "Padaria Aurora"
    assert "Sofia" in r["rascunho"]["prompt"]
    assert r["rascunho"]["submission_id"] == sid
    assert r["rascunho"]["spec"]["products_services"] == "paes e bolos"
    assert r["pode_publicar"] is True, r.get("impedimento")


def test_importar_sem_o_cliente_ter_respondido_explica_o_que_falta(ambiente, mestre_falsa):
    d = _abrir(ambiente)["rascunho"]["id"]
    r = _importar(ambiente, d)

    assert r["success"] is False
    assert "conclua a entrevista" in r["error"].lower()


def test_a_submissao_so_e_consumida_no_publish(ambiente, mestre_falsa):
    """
    Importar e desistir não pode queimar o trabalho que o cliente teve de
    responder — a submissão sumiria da aba sem nunca ter virado agente.
    """
    from data.models import OnboardingSubmission

    sid = _submissao(ambiente)
    d = _abrir(ambiente)["rascunho"]["id"]
    _importar(ambiente, d)

    db = ambiente.Session()
    assert db.query(OnboardingSubmission).get(sid).status == "pending"
    db.close()

    _publicar(ambiente, d)

    db = ambiente.Session()
    assert db.query(OnboardingSubmission).get(sid).status == "processed"
    db.close()


def test_importar_liga_a_qualificacao_que_a_mestre_pediu(ambiente, mestre_falsa):
    """
    REGRESSÃO — o checkbox "Qualificar leads" era uma promessa que nunca se
    cumpria. A tela mandava só `{qualificar: true}`, mas `spec` e
    `qualification_fields` nunca eram escritos, então `build_agent_provisioning`
    recebia `{}` e devolvia `qualification_enabled: False`.
    """
    _submissao(ambiente, loc=LOC_B)
    d = _abrir(ambiente, LOC_B)["rascunho"]["id"]
    r = _importar(ambiente, d, LOC_B)

    assert r["rascunho"]["qualificar"] is True
    assert r["rascunho"]["qualification_fields"][0]["label"] == "Qual o seu bairro?"

    _publicar(ambiente, d, LOC_B)

    db = ambiente.Session()
    a = db.query(AIAgent).filter_by(location_id=LOC_B, channel="whatsapp").first()
    ligada, campos = bool(a.qualification_enabled), list(a.qualification_fields or [])
    db.close()
    assert ligada is True, "o checkbox continua sem ligar nada"
    assert campos and campos[0]["label"] == "Qual o seu bairro?"


# --------------------------------------------------------------------------- #
# Publicar não pode ser destrutivo
# --------------------------------------------------------------------------- #
def test_publicar_por_cima_preserva_a_qualificacao_de_quem_ja_atende(ambiente):
    """
    REGRESSÃO — o agente parava de registrar lead, sem um único erro no log.

    Publicar escrevia as quatro colunas de qualificação incondicionalmente. Com o
    rascunho sem `qualificar`, isso zerava a config que veio do formulário ou da
    curadoria do operador: o agente seguia conversando e só deixava de qualificar.
    """
    db = ambiente.Session()
    db.add(AIAgent(
        location_id=LOC_A, channel="whatsapp", name="Sofia",
        prompt="prompt de producao", is_active=True,
        qualification_enabled=True,
        qualification_fields=[{"label": "Qual o orcamento?", "key": "orcamento"}],
        qualification_pipeline_id="pipe-123", qualification_stage_id="stage-456",
    ))
    db.commit()
    db.close()

    d = _abrir(ambiente)["rascunho"]["id"]
    _salvar(ambiente, d, {"prompt": "prompt novo colado pelo operador", "origem": "manual"})
    r = _publicar(ambiente, d)
    assert r["success"] is True, r
    assert r["qualificacao_preservada"] is True

    db = ambiente.Session()
    a = db.query(AIAgent).filter_by(location_id=LOC_A, channel="whatsapp").first()
    estado = (bool(a.qualification_enabled), a.qualification_pipeline_id,
              a.qualification_stage_id, list(a.qualification_fields or []), a.prompt)
    db.close()

    assert estado[0] is True, "publicar desligou a qualificacao"
    assert estado[1] == "pipe-123" and estado[2] == "stage-456", "funil apagado"
    assert estado[3][0]["label"] == "Qual o orcamento?", "campos curados apagados"
    assert estado[4] == "prompt novo colado pelo operador", "o prompt era pra ter trocado"


def test_agente_novo_sem_qualificacao_nasce_sem_ela(ambiente):
    """Controle: preservar não pode virar 'nunca escrever'."""
    d = _abrir(ambiente)["rascunho"]["id"]
    _salvar(ambiente, d, {"prompt": "um prompt qualquer", "origem": "manual"})
    r = _publicar(ambiente, d)

    assert r["qualificacao_preservada"] is False
    db = ambiente.Session()
    a = db.query(AIAgent).filter_by(location_id=LOC_A, channel="whatsapp").first()
    ligada = bool(a.qualification_enabled)
    db.close()
    assert ligada is False


def test_publicar_nao_apaga_funil_curado_em_tenant_whatsapp_only(ambiente, mestre_falsa):
    """
    REGRESSÃO da minha própria correção — a preservação era all-or-nothing.

    Em `whatsapp_only`, `agent_provisioning` devolve `pronto=True` mesmo SEM funil
    (deliberado: lá o QualifiedLead É o portão). Com a preservação chaveada só em
    `qualification_enabled`, isso entrava no ramo de escrita e gravava
    `pipeline_id=None` por cima do funil que o operador curou — o agente parava de
    criar oportunidade no CRM, em silêncio. Pior: a idempotência do
    `qualification_handler` impede o reenvio mesmo depois de restaurar o funil.
    """
    db = ambiente.Session()
    db.add(AIAgent(
        location_id=LOC_B, channel="whatsapp", name="Sofia", prompt="prompt antigo",
        is_active=True, qualification_enabled=True,
        qualification_fields=[{"label": "Qual o orcamento?", "key": "orcamento",
                               "ghl_field_id": "cf_orc"}],
        qualification_pipeline_id="pipe-123", qualification_stage_id="stage-456",
    ))
    db.commit()
    db.close()

    _submissao(ambiente, loc=LOC_B)
    d = _abrir(ambiente, LOC_B)["rascunho"]["id"]
    _importar(ambiente, d, LOC_B)          # traz qualificar=True + campos novos
    r = _publicar(ambiente, d, LOC_B)
    assert r["success"] is True, r

    db = ambiente.Session()
    a = db.query(AIAgent).filter_by(location_id=LOC_B, channel="whatsapp").first()
    pipe, stage = a.qualification_pipeline_id, a.qualification_stage_id
    campos = list(a.qualification_fields or [])
    db.close()

    assert pipe == "pipe-123", "funil curado foi apagado"
    assert stage == "stage-456", "etapa curada foi apagada"
    assert campos[0].get("ghl_field_id") == "cf_orc", "mapeamento do CRM foi perdido"
    assert r["qualificacao_preservada"] is True


def test_segundo_canal_ainda_consegue_importar_o_que_o_cliente_respondeu(ambiente, mestre_falsa):
    """
    Publicar o WhatsApp marcava a submissão como 'processed', e o Telegram
    respondia "nada para trazer" para sempre — com as respostas do cliente
    intactas no banco. As mesmas respostas servem aos dois canais.
    """
    _submissao(ambiente)
    d1 = _abrir(ambiente)["rascunho"]["id"]
    _importar(ambiente, d1)
    _publicar(ambiente, d1)

    r = _importar(ambiente, _abrir(ambiente)["rascunho"]["id"])
    assert r["success"] is True, r
    assert "Sofia" in r["rascunho"]["prompt"]


def test_reimportar_sem_qualificacao_limpa_os_campos_da_importacao_anterior(ambiente, monkeypatch):
    """Importar só ligava, nunca desligava — sobravam campos de OUTRO negócio."""
    import admin.ai_agent as aa

    campos = [{"label": "Qual o seu bairro?", "description": "", "type": "text"}]

    async def _com(settings, form_data):
        return "prompt A", campos

    async def _sem(settings, form_data):
        return "prompt B", []

    _submissao(ambiente)
    d = _abrir(ambiente)["rascunho"]["id"]

    monkeypatch.setattr(aa, "_run_master", _com, raising=True)
    assert _importar(ambiente, d)["rascunho"]["qualificar"] is True

    monkeypatch.setattr(aa, "_run_master", _sem, raising=True)
    r = _importar(ambiente, d)
    assert r["rascunho"]["qualificar"] is False, "continuou ligado"
    assert r["rascunho"]["qualification_fields"] == [], "sobraram campos do negocio anterior"


def test_importar_para_rascunho_de_outro_tenant_nao_gasta_a_mestre(ambiente, monkeypatch):
    """Id sequencial e adivinhavel: nao pode custar uma chamada de LLM para dizer nao."""
    import admin.ai_agent as aa

    chamadas = []

    async def _conta(settings, form_data):
        chamadas.append(1)
        return "prompt", []

    monkeypatch.setattr(aa, "_run_master", _conta, raising=True)
    _submissao(ambiente, loc=LOC_A)
    alheio = _abrir(ambiente, LOC_B)["rascunho"]["id"]

    r = _importar(ambiente, alheio, LOC_A)

    assert r["success"] is False
    assert chamadas == [], "gastou a Mestre antes de validar o dono do rascunho"
