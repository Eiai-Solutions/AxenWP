"""
Provisionamento do agente a partir do formulário.

O agente nascia com 1 de 35 colunas preenchidas — sem campos de qualificação,
`build_tool_specs` só entregava `escalate_to_human` e ele era incapaz de
qualificar. Estes testes travam a ponte que faltava (perguntas → campos) e o
fail-closed de tudo que depende do CRM.
"""

import pytest

from services.agent_provisioning import (
    build_agent_provisioning,
    derive_qualification_fields,
    match_ghl_field,
    pick_pipeline_stage,
)


class TestDerivarCampos:
    def test_uma_pergunta_por_linha(self):
        campos = derive_qualification_fields("Qual seu nome?\nQual sua empresa?")
        assert [c["label"] for c in campos] == ["Qual seu nome", "Qual sua empresa"]
        assert [c["key"] for c in campos] == ["qual_seu_nome", "qual_sua_empresa"]

    def test_lista_numerada_e_bullets(self):
        campos = derive_qualification_fields("1. Nome\n2) Empresa\n- Orçamento\n* Prazo")
        assert [c["label"] for c in campos] == ["Nome", "Empresa", "Orçamento", "Prazo"]

    def test_varias_perguntas_na_mesma_linha(self):
        campos = derive_qualification_fields("Qual o nome? Qual a empresa? Qual o orçamento?")
        assert len(campos) == 3

    def test_separadas_por_ponto_e_virgula(self):
        campos = derive_qualification_fields("Nome; Empresa; Faturamento")
        assert [c["key"] for c in campos] == ["nome", "empresa", "faturamento"]

    def test_virgula_nao_quebra_a_pergunta(self):
        # "Qual o faturamento, aproximadamente?" é UMA pergunta.
        campos = derive_qualification_fields("Qual o faturamento, aproximadamente?")
        assert len(campos) == 1

    def test_acentos_viram_chave_ascii(self):
        campos = derive_qualification_fields("Qual é o orçamento previsto?")
        assert campos[0]["key"] == "qual_e_o_orcamento_previsto"

    def test_perguntas_duplicadas_nao_colidem_no_schema(self):
        campos = derive_qualification_fields("Nome\nnome\nNOME?")
        assert len(campos) == 1

    def test_cabecalho_nao_vira_campo_obrigatorio(self):
        """
        "Perguntas de qualificação:" viraria um campo OBRIGATÓRIO que o lead nunca
        pode responder — e aí a qualificação nunca completaria para ninguém.
        """
        campos = derive_qualification_fields("Perguntas de qualificação:\n- Nome\n- Empresa")
        assert [c["key"] for c in campos] == ["nome", "empresa"]

    def test_vazio_e_none(self):
        assert derive_qualification_fields("") == []
        assert derive_qualification_fields(None) == []
        assert derive_qualification_fields("   \n  \n") == []

    def test_teto_de_campos(self):
        muitas = "\n".join(f"Pergunta {i}" for i in range(30))
        assert len(derive_qualification_fields(muitas)) == 12


class TestMatchCampoCRM:
    campos_crm = [
        {"id": "F1", "name": "Orçamento"},
        {"id": "F2", "name": "Empresa"},
    ]

    def test_casa_ignorando_acento_e_caixa(self):
        assert match_ghl_field("orcamento", self.campos_crm) == "F1"
        assert match_ghl_field("EMPRESA", self.campos_crm) == "F2"

    def test_descasca_a_pergunta_para_achar_o_campo(self):
        """
        O cliente escreve pergunta ("Qual é o seu orçamento?"), o CRM tem
        substantivo ("Orçamento"). Sem descascar, a taxa de casamento é ~0 e
        nenhum dado sobe para o CRM.
        """
        assert match_ghl_field("Qual é o seu orçamento", self.campos_crm) == "F1"
        assert match_ghl_field("Qual a empresa", self.campos_crm) == "F2"
        assert match_ghl_field("Qual o orçamento previsto", self.campos_crm) == "F1"

    def test_nao_casa_por_aproximacao(self):
        # Casar "Orçamento anual" com "Orçamento" poria a resposta no campo errado.
        assert match_ghl_field("Orçamento anual do projeto", self.campos_crm) is None

    def test_sem_campos_ou_label_vazio(self):
        assert match_ghl_field("Nome", []) is None
        assert match_ghl_field("", self.campos_crm) is None


class TestEscolhaDePipeline:
    def test_um_funil_e_inequivoco(self):
        pipes = [{"id": "P1", "name": "Leads", "stages": [{"id": "S1"}, {"id": "S2"}]}]
        pid, sid, motivo = pick_pipeline_stage(pipes)
        assert (pid, sid, motivo) == ("P1", "S1", None)

    def test_dois_funis_nao_adivinha(self):
        pipes = [
            {"id": "P1", "name": "Leads", "stages": [{"id": "S1"}]},
            {"id": "P2", "name": "Pós-venda", "stages": [{"id": "S9"}]},
        ]
        pid, sid, motivo = pick_pipeline_stage(pipes)
        assert pid is None and sid is None and "escolha" in motivo.lower()

    def test_funil_sem_etapas_e_ignorado(self):
        pipes = [{"id": "P1", "name": "Vazio", "stages": []}]
        pid, sid, motivo = pick_pipeline_stage(pipes)
        assert pid is None and "etapas" in motivo.lower()

    def test_ordena_por_position_e_nao_confia_na_ordem_da_api(self):
        pipes = [{"id": "P1", "name": "Leads", "stages": [
            {"id": "S2", "name": "Contato", "position": 1},
            {"id": "S1", "name": "Novo", "position": 0},
        ]}]
        assert pick_pipeline_stage(pipes)[1] == "S1"

    def test_nunca_escolhe_etapa_terminal(self):
        """Cair em 'Ganho' faria todo lead novo nascer como negócio fechado."""
        pipes = [{"id": "P1", "name": "Leads", "stages": [
            {"id": "SG", "name": "Ganho", "position": 0},
            {"id": "SN", "name": "Novo lead", "position": 1},
        ]}]
        assert pick_pipeline_stage(pipes)[1] == "SN"

    def test_funil_so_com_etapas_de_fechamento_vira_pendencia(self):
        pipes = [{"id": "P1", "name": "Arquivo", "stages": [
            {"id": "S1", "name": "Perdido"}, {"id": "S2", "name": "Cliente"},
        ]}]
        pid, sid, motivo = pick_pipeline_stage(pipes)
        assert pid is None and "entrada" in motivo.lower()

    def test_sem_funis(self):
        assert pick_pipeline_stage([])[0] is None


class TestProvisionamentoCompleto:
    @pytest.mark.asyncio
    async def test_sem_perguntas_agente_so_conversa(self, monkeypatch):
        r = await build_agent_provisioning("loc1", {"qualification_questions": ""})
        assert r["config"] == {"qualification_enabled": False}
        assert r["report"]["qualification_enabled"] is False

    @pytest.mark.asyncio
    async def test_crm_ok_liga_qualificacao_e_mapeia(self, monkeypatch):
        from services import agent_provisioning as prov

        async def _catalogo(loc):
            return {
                "ok": True,
                "pipelines": [{"id": "P1", "name": "Leads", "stages": [{"id": "S1"}]}],
                "fields": [{"id": "F1", "name": "Empresa"}],
            }

        monkeypatch.setattr(prov, "fetch_crm_catalog", _catalogo)
        r = await build_agent_provisioning("loc1", {"qualification_questions": "Nome\nEmpresa"})
        cfg = r["config"]
        assert cfg["qualification_enabled"] is True
        assert cfg["qualification_pipeline_id"] == "P1" and cfg["qualification_stage_id"] == "S1"
        campos = {c["key"]: c for c in cfg["qualification_fields"]}
        assert campos["empresa"]["ghl_field_id"] == "F1"
        assert "ghl_field_id" not in campos["nome"]  # sem casamento confiante
        assert r["report"]["campos_mapeados_no_crm"] == 1

    @pytest.mark.asyncio
    async def test_crm_indisponivel_DESLIGA_a_qualificacao(self, monkeypatch):
        """
        Fail-closed. Ligar sem funil é a pior falha e é silenciosa: o agente diria
        ao lead que registrou, o handler pularia o CRM sem nem logar, e
        `ai_service` PAUSARIA A IA para sempre naquele lead — com a idempotência
        impedindo o reenvio mesmo depois de o operador corrigir o funil.
        """
        from services import agent_provisioning as prov

        async def _catalogo(loc):
            return {"ok": False, "error": "Sem token do CRM — conecte o CRM na instância."}

        monkeypatch.setattr(prov, "fetch_crm_catalog", _catalogo)
        r = await build_agent_provisioning("loc1", {"qualification_questions": "Nome"})
        assert r["config"]["qualification_enabled"] is False
        assert r["config"]["qualification_pipeline_id"] is None
        assert any("token" in p.lower() for p in r["report"]["pendencias"])
        assert any("DESLIGADA" in p for p in r["report"]["pendencias"])

    @pytest.mark.asyncio
    async def test_dois_funis_nao_chuta_e_desliga(self, monkeypatch):
        from services import agent_provisioning as prov

        async def _catalogo(loc):
            return {
                "ok": True,
                "pipelines": [
                    {"id": "P1", "name": "Leads", "stages": [{"id": "S1"}]},
                    {"id": "P2", "name": "Suporte", "stages": [{"id": "S2"}]},
                ],
                "fields": [],
            }

        monkeypatch.setattr(prov, "fetch_crm_catalog", _catalogo)
        r = await build_agent_provisioning("loc1", {"qualification_questions": "Nome"})
        assert r["config"]["qualification_pipeline_id"] is None
        assert r["config"]["qualification_enabled"] is False
        assert any("funis" in p for p in r["report"]["pendencias"])


def test_campos_derivados_geram_a_tool_de_qualificacao():
    """O ponto da mudança: com campos, o agente ganha a tool que não tinha."""
    from types import SimpleNamespace
    from services.agent_engine.tools import build_tool_specs

    campos = derive_qualification_fields("Qual seu nome?\nQual o orçamento?")
    cfg = SimpleNamespace(qualification_enabled=True, qualification_fields=campos)
    nomes = [s.name for s in build_tool_specs(cfg)]
    assert "register_qualified_lead" in nomes

    # E sem campos continua só com o handoff (o estado de hoje).
    vazio = SimpleNamespace(qualification_enabled=False, qualification_fields=None)
    assert [s.name for s in build_tool_specs(vazio)] == ["escalate_to_human"]
