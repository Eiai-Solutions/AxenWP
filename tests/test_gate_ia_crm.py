"""
O portão da IA no modo CRM: 'Status IA' é kill-switch, não opt-in.

Achado em produção (2026-08-18): o Luiz mandou mensagem para o número conectado,
ela chegou, foi espelhada no CRM — e a IA não respondeu. Nenhum erro no log,
porque a única pista era um `logger.debug`.

A causa: o portão exigia `"Ativada"` e **nada no código escreve esse valor**. Os
dois únicos lugares que tocam o campo (`qualification_handler:159`,
`escalation_handler:66`) gravam `"Desativada"` — e o comentário de lá chama isso
de kill-switch. Todo contato do CRM nascia com a IA muda.

Pior: os dois modos do produto tinham o default INVERTIDO um do outro. Em
`whatsapp_only` a IA atende até o lead ser qualificado; em `ghl`, não atendia
ninguém nunca. Mesmo produto, mesma intenção.
"""

import pytest

from services.ghl_service import GHLService


class _GHLFake(GHLService):
    """Sobrescreve só as duas chamadas de rede — o resto é o código real."""

    def __init__(self, field_id, contato):
        self._field_id = field_id
        self._contato = contato
        self.pedidos = []

    async def _get_custom_field_id_by_name(self, location_id, nome):
        self.pedidos.append(("campo", nome))
        return self._field_id

    async def get_contact(self, location_id, contact_id):
        self.pedidos.append(("contato", contact_id))
        return self._contato


def _com(valor, field_id="F1"):
    return _GHLFake(field_id, {"id": "C1", "customFields": [{"id": field_id, "value": valor}]})


@pytest.mark.asyncio
async def test_lead_novo_sem_o_campo_e_atendido():
    """
    REGRESSÃO — este é o caso de TODO lead novo, e era exatamente o que não
    funcionava: contato criado pelo inbound não tem o campo preenchido.
    """
    g = _GHLFake("F1", {"id": "C1", "customFields": []})

    assert await g.is_ai_active_for_contact("loc", "C1") is True


@pytest.mark.asyncio
async def test_desativada_pausa():
    assert await _com("Desativada").is_ai_active_for_contact("loc", "C1") is False


@pytest.mark.asyncio
async def test_ativada_atende():
    assert await _com("Ativada").is_ai_active_for_contact("loc", "C1") is True


@pytest.mark.asyncio
@pytest.mark.parametrize("valor", ["desativada", "DESATIVADA", "  Desativada  "])
async def test_a_pausa_nao_depende_de_caixa_nem_espaco(valor):
    assert await _com(valor).is_ai_active_for_contact("loc", "C1") is False


@pytest.mark.asyncio
async def test_campo_de_multipla_escolha_devolve_lista():
    """O GHL manda lista quando o campo é multi-select."""
    assert await _com(["Desativada"]).is_ai_active_for_contact("loc", "C1") is False
    assert await _com(["Ativada"]).is_ai_active_for_contact("loc", "C1") is True


@pytest.mark.asyncio
async def test_valor_vazio_nao_pausa():
    """Campo existe, valor em branco: nunca foi pausado."""
    assert await _com("").is_ai_active_for_contact("loc", "C1") is True
    assert await _com(None).is_ai_active_for_contact("loc", "C1") is True


@pytest.mark.asyncio
async def test_sem_o_campo_na_location_a_ia_atende():
    """Não ter interruptor não pode significar produto mudo."""
    g = _GHLFake(None, {"id": "C1", "customFields": []})

    assert await g.is_ai_active_for_contact("loc", "C1") is True
    assert ("contato", "C1") not in g.pedidos, "buscou o contato sem precisar"


@pytest.mark.asyncio
async def test_contato_ilegivel_silencia_de_proposito():
    """
    `get_contact` devolve None tanto para erro quanto para inexistente. Calamos:
    a pausa protege um handoff em curso, e falar por cima de um humano é pior que
    o silêncio.
    """
    g = _GHLFake("F1", None)

    assert await g.is_ai_active_for_contact("loc", "C1") is False


@pytest.mark.asyncio
async def test_campo_de_outro_id_nao_confunde():
    g = _GHLFake("F1", {"id": "C1", "customFields": [
        {"id": "OUTRO", "value": "Desativada"},
        {"id": "F1", "value": "Ativada"},
    ]})

    assert await g.is_ai_active_for_contact("loc", "C1") is True
