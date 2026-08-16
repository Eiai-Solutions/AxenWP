"""
As etapas de criação do agente são derivadas do tenant, não fixas.

O que estes testes travam é a regra que resolve o problema original: a tela
perguntava funil de CRM para tenant que não tem CRM, porque a config tinha forma
fixa herdada de quando o produto era só GoHighLevel.

A regra: cada etapa pergunta o que ela é capaz de aplicar. Sem CRM a etapa de
qualificação NÃO some — ela muda de forma, porque qualificar sem CRM significa
outra coisa (o `QualifiedLead` é o destino e o gate).
"""

from types import SimpleNamespace

import pytest

from services.agent_wizard import (
    CANAL,
    IDENTIDADE,
    QUALIFICACAO,
    REVISAO,
    canais_disponiveis,
    etapas_para,
    pode_publicar,
    tem_crm,
)


def _tenant(**over):
    base = dict(
        location_id="loc1", mode="ghl", pit_token="pit-x", access_token=None,
        whatsapp_provider="waha", waha_base_url="https://waha", waha_session="s",
        waha_api_key="k", zapi_instance_id=None, zapi_token=None,
        telegram_bot_token=None, telegram_bot_username=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _ids(tenant):
    return [e.id for e in etapas_para(tenant)]


def _etapa(tenant, qual):
    return next(e for e in etapas_para(tenant) if e.id == qual)


# --------------------------------------------------------------------------- #
# CRM: modo + credencial, não só o modo
# --------------------------------------------------------------------------- #
def test_whatsapp_only_nunca_tem_crm():
    assert tem_crm(_tenant(mode="whatsapp_only", pit_token="pit-x")) is False


def test_modo_ghl_sem_credencial_nao_conta_como_crm():
    """
    Marcar `ghl` sem concluir o OAuth é estado real. Perguntar funil aí seria
    oferecer uma lista vazia.
    """
    assert tem_crm(_tenant(mode="ghl", pit_token=None, access_token=None)) is False


def test_modo_ghl_com_pit_ou_oauth_tem_crm():
    assert tem_crm(_tenant(pit_token="pit-x", access_token=None)) is True
    assert tem_crm(_tenant(pit_token=None, access_token="oauth-x")) is True


# --------------------------------------------------------------------------- #
# A etapa que muda de FORMA (o pedido original)
# --------------------------------------------------------------------------- #
def test_com_crm_a_qualificacao_pede_funil():
    e = _etapa(_tenant(), QUALIFICACAO)
    assert e.variante == "crm"
    assert e.dados["precisa_funil"] is True


def test_sem_crm_a_qualificacao_NAO_some_muda_de_forma(tmp_path):
    """
    Esconder a etapa seria errado: o tenant sem CRM qualifica sim — o lead é
    marcado e a IA pausa. O que não existe é funil para escolher.
    """
    e = _etapa(_tenant(mode="whatsapp_only"), QUALIFICACAO)
    assert e.variante == "sem_crm"
    assert e.dados["precisa_funil"] is False
    assert "pausa" in e.descricao.lower(), "nao explica o que acontece com o lead"


def test_a_etapa_de_qualificacao_existe_nos_dois_casos():
    assert QUALIFICACAO in _ids(_tenant())
    assert QUALIFICACAO in _ids(_tenant(mode="whatsapp_only"))


# --------------------------------------------------------------------------- #
# Canal
# --------------------------------------------------------------------------- #
def test_um_canal_so_nao_vira_pergunta():
    e = _etapa(_tenant(), CANAL)
    assert e.variante == "unico"
    assert e.dados["escolhido"] == "whatsapp"


def test_dois_canais_viram_escolha():
    e = _etapa(_tenant(telegram_bot_token="123:abc", telegram_bot_username="bot"), CANAL)
    assert e.variante == "escolha"
    assert {c["canal"] for c in e.dados["canais"]} == {"whatsapp", "telegram"}


def test_sem_canal_nenhum_bloqueia_antes_de_perder_o_tempo_da_pessoa():
    """
    Deixar preencher tudo e falhar no fim é o pior desfecho possível — a pessoa
    investe a entrevista inteira para descobrir que não dava.
    """
    t = _tenant(whatsapp_provider=None, waha_base_url=None, waha_session=None,
                waha_api_key=None, telegram_bot_token=None)
    etapas = etapas_para(t)
    assert len(etapas) == 1
    assert etapas[0].variante == "bloqueado"
    assert IDENTIDADE not in [e.id for e in etapas]


def test_telegram_sozinho_tambem_serve():
    t = _tenant(whatsapp_provider=None, waha_base_url=None, waha_session=None,
                waha_api_key=None, telegram_bot_token="123:abc")
    assert [c["canal"] for c in canais_disponiveis(t)] == ["telegram"]
    assert _etapa(t, CANAL).variante == "unico"


# --------------------------------------------------------------------------- #
# Ordem e completude
# --------------------------------------------------------------------------- #
def test_a_ordem_e_canal_identidade_qualificacao_revisao():
    assert _ids(_tenant()) == [CANAL, IDENTIDADE, QUALIFICACAO, REVISAO]


def test_identidade_oferece_as_tres_portas_com_a_entrevista_recomendada():
    e = _etapa(_tenant(), IDENTIDADE)
    portas = {p["id"]: p for p in e.dados["portas"]}
    assert set(portas) == {"entrevista", "formulario", "manual"}
    assert portas["entrevista"]["recomendado"] is True


# --------------------------------------------------------------------------- #
# Guard de publicação
# --------------------------------------------------------------------------- #
def test_nao_publica_sem_prompt():
    t = _tenant()
    ok, motivo = pode_publicar({"channel": "whatsapp", "prompt": "  "}, etapas_para(t))
    assert ok is False and "prompt" in motivo.lower()


def test_nao_publica_sem_canal_escolhido():
    t = _tenant(telegram_bot_token="123:abc")
    ok, motivo = pode_publicar({"prompt": "um prompt de verdade"}, etapas_para(t))
    assert ok is False and "canal" in motivo.lower()


def test_nao_publica_se_o_tenant_nao_tem_canal():
    t = _tenant(whatsapp_provider=None, waha_base_url=None, waha_session=None,
                waha_api_key=None)
    ok, _ = pode_publicar({"channel": "whatsapp", "prompt": "x" * 50}, etapas_para(t))
    assert ok is False


def test_publica_quando_esta_completo():
    t = _tenant()
    ok, motivo = pode_publicar(
        {"channel": "whatsapp", "prompt": "Você é a Sofia, SDR da empresa..."},
        etapas_para(t),
    )
    assert ok is True and motivo is None
