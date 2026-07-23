"""
AgentSpec + caller da Mestre + fusão com o provisionamento.

A regra que estes testes protegem: o LLM só declara INTENÇÃO. IDs de CRM, chaves
de campo e flags de ativação NÃO existem no schema — resolvidos por código.
"""

import pytest


class TestAgentSpecSchema:
    def test_schema_nao_expoe_nenhum_id_de_crm_nem_flag(self):
        from utils.agent_spec import AgentSpec

        props = set(AgentSpec.model_json_schema()["properties"].keys())
        proibidos = {
            "qualification_pipeline_id", "qualification_stage_id", "ghl_field_id",
            "qualification_enabled", "is_active", "agent_engine", "anthropic_api_key", "key",
        }
        assert proibidos.isdisjoint(props), f"schema expõe campo proibido: {proibidos & props}"

    def test_campo_de_qualificacao_so_tem_intencao(self):
        from utils.agent_spec import AgentSpec

        defs = AgentSpec.model_json_schema()["$defs"]["QualFieldSpec"]["properties"]
        assert set(defs.keys()) == {"label", "description", "type"}

    def test_extrai_intencao_ignorando_label_vazio_e_normalizando_tipo(self):
        from utils.agent_spec import AgentSpec, spec_fields_as_intent

        s = AgentSpec(
            system_prompt="x" * 50, wants_qualification=True,
            qualification_fields=[
                {"label": "Orçamento", "description": "quanto investe", "type": "NUMBER"},
                {"label": "  ", "type": "text"},          # vazio: descartado
                {"label": "Segmento", "type": "xyz"},     # tipo inválido -> text
            ],
        )
        intent = spec_fields_as_intent(s)
        assert [f["label"] for f in intent] == ["Orçamento", "Segmento"]
        assert intent[0]["type"] == "number" and intent[1]["type"] == "text"

    def test_register_nao_colide_com_pydantic(self):
        # tone_register (não 'register', que sombreia BaseModel) — sem warning.
        from utils.agent_spec import AgentSpec
        assert AgentSpec(system_prompt="x" * 50).tone_register == "neutro"


class TestFusaoProvisionamento:
    @pytest.mark.asyncio
    async def test_fields_override_do_spec_vira_a_fonte(self, monkeypatch):
        from services import agent_provisioning as prov

        async def _catalogo(loc):
            return {
                "ok": True,
                "pipelines": [{"id": "P1", "name": "Leads", "stages": [{"id": "S1", "name": "Novo"}]}],
                "fields": [{"id": "F1", "name": "Orçamento"}],
            }

        monkeypatch.setattr(prov, "fetch_crm_catalog", _catalogo)
        # A Mestre manda os campos como intenção; o parser NÃO é usado.
        override = [
            {"label": "Orçamento", "description": "quanto investe", "type": "number"},
            {"label": "Empresa", "type": "text"},
        ]
        r = await prov.build_agent_provisioning("loc1", {"qualification_questions": "IGNORADO"}, fields_override=override)
        cfg = r["config"]
        assert cfg["qualification_enabled"] is True
        campos = {c["key"]: c for c in cfg["qualification_fields"]}
        # key é derivada por código, não pelo LLM
        assert set(campos) == {"orcamento", "empresa"}
        # descrição/tipo do Spec são preservados
        assert campos["orcamento"]["description"] == "quanto investe"
        assert campos["orcamento"]["type"] == "number"
        # ghl_field_id resolvido por match contra o CRM, não pelo Spec
        assert campos["orcamento"]["ghl_field_id"] == "F1"

    @pytest.mark.asyncio
    async def test_override_vazio_desliga_sem_cair_no_parser(self, monkeypatch):
        from services import agent_provisioning as prov
        # fields_override=[] significa "a Mestre decidiu: sem qualificação".
        # Não pode cair no parser do texto livre.
        r = await prov.build_agent_provisioning("loc1", {"qualification_questions": "Nome\nEmpresa"}, fields_override=[])
        assert r["config"]["qualification_enabled"] is False

    @pytest.mark.asyncio
    async def test_sem_override_usa_o_parser(self, monkeypatch):
        from services import agent_provisioning as prov

        async def _catalogo(loc):
            return {"ok": False, "error": "sem CRM"}

        monkeypatch.setattr(prov, "fetch_crm_catalog", _catalogo)
        # fields_override=None → fallback para o texto livre do operador.
        r = await prov.build_agent_provisioning("loc1", {"qualification_questions": "Nome\nEmpresa"})
        assert [c["key"] for c in r["config"]["qualification_fields"]] == ["nome", "empresa"]


class TestCallerGate:
    def test_gate_exige_chave_E_toggle_proprio(self, monkeypatch):
        from services import master_engine

        # Sem chave: nunca.
        monkeypatch.setattr(master_engine, "_resolve_master_key", lambda: None)
        monkeypatch.setenv("MASTER_ENGINE", "anthropic")
        assert master_engine.is_configured() is False

        # Com chave mas SEM toggle: segue legado (não vira structured sozinho só
        # porque a chave do MOTOR apareceu).
        monkeypatch.setattr(master_engine, "_resolve_master_key", lambda: "sk-ant-xxx")
        monkeypatch.delenv("MASTER_ENGINE", raising=False)
        monkeypatch.delenv("MASTER_USE_SPEC", raising=False)
        assert master_engine.is_configured() is False

        # Chave + toggle explícito: structured.
        monkeypatch.setenv("MASTER_ENGINE", "anthropic")
        assert master_engine.is_configured() is True
        monkeypatch.delenv("MASTER_ENGINE", raising=False)
        monkeypatch.setenv("MASTER_USE_SPEC", "1")
        assert master_engine.is_configured() is True

    @pytest.mark.asyncio
    async def test_generate_spec_sem_chave_levanta(self, monkeypatch):
        from services import master_engine
        monkeypatch.setattr(master_engine, "_resolve_master_key", lambda: None)
        with pytest.raises(RuntimeError):
            await master_engine.generate_agent_spec({"company_name": "X"})

    @pytest.mark.asyncio
    async def test_generate_spec_devolve_o_parsed_output(self, monkeypatch):
        from services import master_engine
        from utils.agent_spec import AgentSpec

        monkeypatch.setattr(master_engine, "_resolve_master_key", lambda: "sk-ant-xxx")

        esperado = AgentSpec(system_prompt="Você é a Sofia, atendente..." + "x" * 40,
                             wants_qualification=True)

        class _Resp:
            parsed_output = esperado
            stop_reason = "end_turn"

        class _Msgs:
            async def parse(self, **kw):
                # a chamada não pode mandar cache_control (decisão: sem caching)
                assert "cache_control" not in kw
                assert kw["output_format"] is AgentSpec
                return _Resp()

        class _Client:
            def __init__(self, **kw): self.messages = _Msgs()

        monkeypatch.setattr(master_engine, "AsyncAnthropic", _Client, raising=False)
        import anthropic
        monkeypatch.setattr(anthropic, "AsyncAnthropic", _Client)

        spec = await master_engine.generate_agent_spec({"company_name": "X"})
        assert spec.system_prompt.startswith("Você é a Sofia")

    @pytest.mark.asyncio
    async def test_spec_vazio_levanta(self, monkeypatch):
        from services import master_engine

        monkeypatch.setattr(master_engine, "_resolve_master_key", lambda: "sk-ant-xxx")

        class _Resp:
            parsed_output = None       # a API truncou/recusou → sem objeto
            stop_reason = "max_tokens"

        class _Msgs:
            async def parse(self, **kw): return _Resp()

        class _Client:
            def __init__(self, **kw): self.messages = _Msgs()

        import anthropic
        monkeypatch.setattr(anthropic, "AsyncAnthropic", _Client)
        with pytest.raises(RuntimeError):
            await master_engine.generate_agent_spec({"company_name": "X"})
