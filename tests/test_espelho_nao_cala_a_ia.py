"""
Falha ao espelhar no CRM não pode deixar o lead sem resposta.

Os dois caminhos de entrada faziam coisas OPOSTAS com a mesma falha:

· WAHA (`services/inbound_pipeline.py`) logava, contava a métrica e seguia — com
  o motivo escrito: espelho é registro, não atendimento.
· Z-API (`webhooks/zapi_receiver.py`) dava `return` e abortava o turno inteiro.

Abortar custava três coisas de uma vez: o cliente ficava sem resposta, a IA nem
rodava, e a mensagem nem chegava ao `msglog_persist` — o painel próprio perdia o
registro justamente quando o CRM já tinha perdido.

Isto passa a importar muito mais com um CRM novo: enquanto o Manager não estiver
estável, cada hipo dele calaria a IA no caminho Z-API.
"""

import pytest


def _fonte(caminho: str) -> str:
    import pathlib
    return pathlib.Path(caminho).read_text()


def test_o_caminho_zapi_nao_aborta_o_turno_quando_o_espelho_falha():
    """
    Guarda estrutural: o `return` no ramo de falha do espelho é exatamente o bug.
    Um teste de comportamento aqui exigiria simular o pipeline inteiro da Z-API
    (contato, GHL, debounce, agente); a asserção sobre a forma do ramo é o que
    pega a regressão de verdade, e é honesta sobre o que verifica.
    """
    fonte = _fonte("webhooks/zapi_receiver.py")
    i = fonte.find("if not resp or resp.get(\"error\"):")
    assert i > 0, "o ramo de falha do espelho sumiu — reescreva este teste"

    ramo = fonte[i:i + 1400]
    assert "millochat_crm_mirror_failed_total" in ramo, (
        "a falha de espelho não é contada — sem métrica ela é invisível"
    )
    # Procura uma INSTRUÇÃO `return`, não a palavra: o comentário que explica o
    # bug contém "return" e faria este teste passar/falhar pelo motivo errado.
    instrucoes = [
        linha.strip() for linha in ramo.splitlines()
        if linha.strip() and not linha.strip().startswith("#")
    ]
    abortos = [i for i in instrucoes if i == "return" or i.startswith("return ")]
    assert not abortos, (
        f"o turno é abortado quando o CRM falha — o lead fica sem resposta: {abortos}"
    )


def test_os_dois_caminhos_contam_a_MESMA_metrica():
    """
    Se cada caminho contar a sua, o operador não consegue perguntar "o CRM está
    falhando?" sem saber por qual porta a mensagem entrou.
    """
    assert "millochat_crm_mirror_failed_total" in _fonte("webhooks/zapi_receiver.py")
    assert "millochat_crm_mirror_failed_total" in _fonte("services/inbound_pipeline.py")


def test_o_caminho_WAHA_continua_seguindo_em_frente():
    """A referência de comportamento — se ela mudar, este arquivo inteiro mente."""
    fonte = _fonte("services/inbound_pipeline.py")
    i = fonte.find("millochat_crm_mirror_failed_total")
    assert i > 0
    # Depois de contar a falha, o pipeline segue: a próxima coisa não é um return.
    depois = fonte[i:i + 400]
    assert not depois.lstrip().startswith("return"), "o caminho WAHA passou a abortar"
