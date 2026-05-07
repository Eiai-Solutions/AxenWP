"""Tests for services/ai_service.py — pure functions and unit logic."""

import pytest
from services.audio_handler import contains_special_content


class TestContainsSpecialContent:
    """Tests for the TTS-unfriendly content detector."""

    # --- Should detect (return True) ---

    def test_detects_https_url(self):
        assert contains_special_content("Acesse https://example.com para mais info")

    def test_detects_www_url(self):
        assert contains_special_content("Visite www.exemplo.com.br")

    def test_detects_com_br_domain(self):
        assert contains_special_content("O site é empresa.com.br")

    def test_detects_generic_domain(self):
        assert contains_special_content("Veja em app.io")

    def test_detects_email(self):
        assert contains_special_content("Mande email para joao@empresa.com")

    def test_detects_reais(self):
        assert contains_special_content("O preço é R$ 1.500,00")

    def test_detects_reais_no_space(self):
        assert contains_special_content("Custa R$200,00")

    def test_detects_brazilian_number_format(self):
        assert contains_special_content("Total: 1.500,00")

    def test_detects_cep(self):
        assert contains_special_content("CEP 01234-567")

    def test_detects_cep_no_dash(self):
        assert contains_special_content("CEP 01234567")

    def test_detects_cpf(self):
        assert contains_special_content("CPF: 123.456.789-00")

    def test_detects_cnpj(self):
        assert contains_special_content("CNPJ: 12.345.678/0001-90")

    def test_detects_phone(self):
        assert contains_special_content("Ligue para (11) 99999-9999")

    def test_detects_address_rua(self):
        assert contains_special_content("Endereço: Rua das Flores, 123")

    def test_detects_address_avenida(self):
        assert contains_special_content("Estamos na Avenida Paulista 1000")

    # --- Should NOT detect (return False) ---

    def test_plain_text(self):
        assert not contains_special_content("Olá, tudo bem? Como posso ajudar?")

    def test_simple_numbers(self):
        assert not contains_special_content("Temos 3 opções disponíveis")

    def test_question(self):
        assert not contains_special_content("Qual o horário de funcionamento?")

    def test_empty_string(self):
        assert not contains_special_content("")
