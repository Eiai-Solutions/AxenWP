"""Tests for utils/master_prompt.py — register detection and context building."""

from utils.master_prompt import (
    _detect_register,
    build_company_context,
    build_messages,
    build_improve_messages,
)


class TestRegisterDetection:
    def test_consultoria_imigracao_is_premium(self):
        fd = {
            "industry": "Consultoria de imigração e investimento internacional",
            "target_audience": "Empresários e investidores",
        }
        assert _detect_register(fd) == "premium"

    def test_business_plan_is_premium(self):
        fd = {
            "industry": "Business plans para vistos americanos (E-2, L-1, EB-5)",
            "company_description": "Atendemos investidores...",
        }
        assert _detect_register(fd) == "premium"

    def test_advocacia_is_premium(self):
        fd = {"industry": "Advocacia tributária"}
        assert _detect_register(fd) == "premium"

    def test_academia_is_casual(self):
        fd = {
            "industry": "Academia / fitness",
            "target_audience": "Pessoas que querem treinar",
        }
        assert _detect_register(fd) == "casual"

    def test_delivery_is_casual(self):
        fd = {"industry": "Delivery de comida japonesa"}
        assert _detect_register(fd) == "casual"

    def test_estetica_is_casual(self):
        fd = {"industry": "Salão de beleza e estética"}
        assert _detect_register(fd) == "casual"

    def test_helpdesk_is_support(self):
        fd = {
            "industry": "SaaS B2B",
            "company_description": "Suporte técnico 24/7 para nossos clientes",
        }
        assert _detect_register(fd) == "support"

    def test_sac_keyword_wins_over_other(self):
        # SAC sempre vence (é o caso mais específico)
        fd = {
            "industry": "Consultoria",
            "company_description": "Atendimento técnico SAC para clientes corporativos",
        }
        assert _detect_register(fd) == "support"

    def test_empty_falls_to_neutro(self):
        assert _detect_register({}) == "neutro"

    def test_unknown_industry_falls_to_neutro(self):
        fd = {"industry": "Coisa qualquer que não tem keyword"}
        assert _detect_register(fd) == "neutro"


class TestContextBuilder:
    def test_includes_register_label(self):
        fd = {"industry": "Consultoria de imigração", "company_name": "X"}
        ctx = build_company_context(fd)
        assert "REGISTRO DETECTADO: PREMIUM" in ctx

    def test_inbound_default(self):
        ctx = build_company_context({"company_name": "X"})
        assert "INBOUND" in ctx

    def test_outbound_label(self):
        ctx = build_company_context({"company_name": "X", "agent_type": "outbound"})
        assert "OUTBOUND" in ctx

    def test_handles_missing_fields(self):
        ctx = build_company_context({})
        assert "Nenhuma" in ctx or "Não" in ctx
        # Não deve crashear


class TestBuildMessages:
    def test_messages_have_system_and_user(self):
        msgs = build_messages({"industry": "Academia"})
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"

    def test_user_message_includes_company_context(self):
        msgs = build_messages({"company_name": "TesteCo", "industry": "Consultoria"})
        assert "TesteCo" in msgs[1]["content"]
        assert "Consultoria" in msgs[1]["content"]


class TestImproveMessages:
    def test_diagnose_mode(self):
        msgs = build_improve_messages(
            form_data={"company_name": "X"},
            current_prompt="prompt aqui",
            conversation_history=[],
            mode="diagnose",
        )
        assert "MODO: diagnose" in msgs[1]["content"]

    def test_apply_mode(self):
        msgs = build_improve_messages(
            form_data={"company_name": "X"},
            current_prompt="prompt aqui",
            conversation_history=[],
            mode="apply",
        )
        assert "MODO: apply" in msgs[1]["content"]

    def test_history_formatted(self):
        msgs = build_improve_messages(
            form_data={},
            current_prompt="",
            conversation_history=[
                {"role": "human", "content": "oi"},
                {"role": "ai", "content": "olá"},
            ],
        )
        assert "Lead: oi" in msgs[1]["content"]
        assert "Agente: olá" in msgs[1]["content"]

    def test_empty_history(self):
        msgs = build_improve_messages(
            form_data={},
            current_prompt="",
            conversation_history=[],
        )
        assert "Nenhum histórico" in msgs[1]["content"]

    def test_feedback_included(self):
        msgs = build_improve_messages(
            form_data={},
            current_prompt="",
            conversation_history=[],
            user_feedback="agente tá agressivo demais",
        )
        assert "agressivo demais" in msgs[1]["content"]
