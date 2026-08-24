"""
Mensagem repetida não é respondida duas vezes.

Assinatura de webhook prova QUEM mandou — não quando, nem quantas vezes. Um corpo
assinado capturado continua assinado para sempre; reenviá-lo N vezes fazia o
agente responder N vezes, com N chamadas de LLM na conta do cliente e N mensagens
saindo pelo número dele. Fechar o HMAC sem fechar isto trocaria "qualquer um" por
"qualquer um que tenha visto um request".

De quebra cobre a retentativa honesta: provedor que não recebeu o nosso 200
reentrega o mesmo webhook, e sem dedup a reentrega vira resposta duplicada.
"""

import time

import pytest

from services import inbound_pipeline as ip


@pytest.fixture(autouse=True)
def limpo():
    ip._inbound_message_ids.clear()
    yield
    ip._inbound_message_ids.clear()


def test_a_segunda_vez_e_reconhecida():
    assert ip.ja_processada("loc1", "MSG-1") is False, "a primeira vez tem que passar"
    assert ip.ja_processada("loc1", "MSG-1") is True


def test_o_id_e_escopado_por_tenant():
    """
    Id de mensagem é único no provedor, não entre tenants. Sem o escopo, a
    mensagem de um cliente calaria a de outro — e o sintoma seria "o agente às
    vezes não responde", impossível de rastrear.
    """
    assert ip.ja_processada("loc1", "MSG-1") is False
    assert ip.ja_processada("loc2", "MSG-1") is False, "a msg de outro tenant foi engolida"
    assert ip.ja_processada("loc2", "MSG-1") is True


def test_sem_id_sempre_passa():
    """
    Provedor que não manda id não dá o que deduplicar. Deixar passar uma repetida
    é melhor que engolir mensagem real de lead.
    """
    assert ip.ja_processada("loc1", None) is False
    assert ip.ja_processada("loc1", None) is False
    assert ip.ja_processada("loc1", "") is False


def test_a_limpeza_expira_os_antigos_e_preserva_os_novos():
    ip.ja_processada("loc1", "VELHA")
    ip.ja_processada("loc1", "NOVA")
    ip._inbound_message_ids["loc1:VELHA"] = time.time() - (ip._INBOUND_IDS_MAX_AGE + 60)

    ip.cleanup_stale_entries()

    assert "loc1:VELHA" not in ip._inbound_message_ids
    assert "loc1:NOVA" in ip._inbound_message_ids, "expirou entrada que ainda vale"


def test_o_cap_limita_a_memoria_e_descarta_a_mais_antiga():
    """`OrderedDict` sem teto num pico de tráfego é vazamento de memória."""
    cap = ip._INBOUND_IDS_HARD_CAP
    for i in range(cap + 10):
        ip.ja_processada("loc1", f"M{i}")
    assert len(ip._inbound_message_ids) <= cap
    assert "loc1:M0" not in ip._inbound_message_ids, "descartou a mais NOVA em vez da mais velha"
    assert f"loc1:M{cap + 9}" in ip._inbound_message_ids


@pytest.mark.asyncio
async def test_handle_inbound_para_na_repeticao_antes_de_tocar_no_CRM(monkeypatch):
    """
    O ponto de parada importa: tem que ser ANTES de resolver contato, espelhar no
    CRM e agendar a IA. Parar depois já teria gastado tudo o que interessa.
    """
    from types import SimpleNamespace

    tocou = []

    async def _nao_deveria(*a, **kw):
        tocou.append("resolve_contact_id")
        return "c1"

    monkeypatch.setattr(ip, "resolve_contact_id", _nao_deveria)

    adapter = SimpleNamespace(provider="waha")
    tenant = SimpleNamespace(mode="ghl", location_id="loc1")
    pm = SimpleNamespace(
        is_group=False, from_me=False, provider_message_id="MSG-9",
        text="oi", attachments=None, location_id="loc1", channel="whatsapp",
        sender_id="5511999999999", sender_name="Fulano", sender_lid=None,
    )

    ip.ja_processada("loc1", "MSG-9")          # já veio uma vez
    await ip.handle_inbound(adapter, tenant, pm)

    assert tocou == [], "a repetição chegou a mexer no CRM"
