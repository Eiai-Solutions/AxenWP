"""
Uma pessoa, duas identidades: telefone e @lid.

O WhatsApp nem sempre entrega o número — às vezes o remetente chega só como
@lid. Se o mapeamento guardasse uma identidade só, a MESMA pessoa viraria um
contato novo no CRM toda vez que aparecesse pela outra ponta.

Estes testes travam as duas direções da reconciliação, que é o que impede a
duplicata.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from data.models import Base, ContactMapping

LOC = "loc1"
FONE = "554797838884"
LID = "198101675561023@lid"


@pytest.fixture
def tm(monkeypatch, tmp_path):
    """TokenManager falando com um SQLite temporário."""
    from auth import token_manager as tm_mod

    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(tm_mod, "SessionLocal", Session)
    return tm_mod.token_manager


def linhas(tm):
    from auth import token_manager as tm_mod
    db = tm_mod.SessionLocal()
    try:
        return db.query(ContactMapping).all()
    finally:
        db.close()


class TestBuscaPelasDuasIdentidades:
    def test_acha_pelo_telefone(self, tm):
        tm.save_contact_mapping(LOC, FONE, "C1", lid=LID)
        assert tm.get_mapped_contact_id(LOC, FONE) == "C1"

    def test_acha_pelo_lid_quando_gravado_sob_o_telefone(self, tm):
        """O caso que o operador levantou: chega sem número, tem que achar mesmo assim."""
        tm.save_contact_mapping(LOC, FONE, "C1", lid=LID)
        assert tm.get_mapped_contact_id(LOC, LID) == "C1"

    def test_acha_pelo_telefone_quando_gravado_sob_o_lid(self, tm):
        # Direção inversa: entrou como @lid, depois apareceu com número.
        tm.save_contact_mapping(LOC, LID, "C1", lid=LID)
        assert tm.get_mapped_contact_id(LOC, LID) == "C1"

    def test_identidade_desconhecida_nao_inventa_contato(self, tm):
        tm.save_contact_mapping(LOC, FONE, "C1", lid=LID)
        assert tm.get_mapped_contact_id(LOC, "5511000000000") is None

    def test_nao_vaza_entre_tenants(self, tm):
        tm.save_contact_mapping(LOC, FONE, "C1", lid=LID)
        assert tm.get_mapped_contact_id("outro_loc", FONE) is None
        assert tm.get_mapped_contact_id("outro_loc", LID) is None


class TestRecuperarTelefonePeloLid:
    def test_devolve_o_telefone_vinculado(self, tm):
        tm.save_contact_mapping(LOC, FONE, "C1", lid=LID)
        assert tm.get_phone_by_lid(LOC, LID) == FONE

    def test_lid_sem_telefone_conhecido_devolve_none(self, tm):
        # Linha gravada só com o @lid: não há número para responder.
        tm.save_contact_mapping(LOC, LID, "C1", lid=LID)
        assert tm.get_phone_by_lid(LOC, LID) is None

    def test_lid_nunca_visto(self, tm):
        assert tm.get_phone_by_lid(LOC, "999@lid") is None


class TestGravacao:
    def test_nao_apaga_lid_ja_conhecido(self, tm):
        """Uma mensagem sem @lid não pode fazer a gente esquecer o vínculo."""
        tm.save_contact_mapping(LOC, FONE, "C1", lid=LID)
        tm.save_contact_mapping(LOC, FONE, "C1")          # sem lid
        assert tm.get_phone_by_lid(LOC, LID) == FONE

    def test_atualiza_contato_sem_duplicar_linha(self, tm):
        tm.save_contact_mapping(LOC, FONE, "C1", lid=LID)
        tm.save_contact_mapping(LOC, FONE, "C2", lid=LID)
        assert len(linhas(tm)) == 1
        assert tm.get_mapped_contact_id(LOC, FONE) == "C2"


class TestDelecao:
    def test_apagar_pelo_telefone_leva_junto_a_linha_do_lid(self, tm):
        """
        Contato deletado no CRM: se a linha alias sobrevivesse, a próxima mensagem
        reusaria um contact_id morto — em loop, sem auto-cura.
        """
        tm.save_contact_mapping(LOC, FONE, "C1", lid=LID)
        tm.delete_contact_mapping(LOC, FONE)
        assert tm.get_mapped_contact_id(LOC, FONE) is None
        assert tm.get_mapped_contact_id(LOC, LID) is None

    def test_apagar_pelo_lid_limpa_a_linha_do_telefone(self, tm):
        tm.save_contact_mapping(LOC, FONE, "C1", lid=LID)
        tm.delete_contact_mapping(LOC, LID)
        assert tm.get_mapped_contact_id(LOC, FONE) is None
        assert tm.get_mapped_contact_id(LOC, LID) is None


class TestReconciliacaoNoPipeline:
    """resolve_contact_id não pode criar contato quando já conhece a pessoa."""

    @pytest.mark.asyncio
    async def test_lid_nao_resolvido_reencontra_contato_criado_pelo_telefone(self, tm, monkeypatch):
        from services import inbound_pipeline as pipe

        tm.save_contact_mapping(LOC, FONE, "C1", lid=LID)

        async def nunca_cria(*a, **kw):
            raise AssertionError("não deveria criar contato — a pessoa já é conhecida")

        monkeypatch.setattr(pipe.ghl_service, "create_contact", nunca_cria)
        monkeypatch.setattr(pipe.ghl_service, "search_contact_by_phone", nunca_cria)

        # Mensagem chega só com o @lid (resolução falhou).
        assert await pipe.resolve_contact_id(LOC, LID, "") == "C1"

    @pytest.mark.asyncio
    async def test_telefone_reencontra_contato_criado_pelo_lid(self, tm, monkeypatch):
        from services import inbound_pipeline as pipe

        # Primeiro contato nasceu sem número, identificado pelo @lid.
        tm.save_contact_mapping(LOC, LID, "C1", lid=LID)

        async def nunca_cria(*a, **kw):
            raise AssertionError("não deveria criar contato — a pessoa já é conhecida")

        monkeypatch.setattr(pipe.ghl_service, "create_contact", nunca_cria)
        monkeypatch.setattr(pipe.ghl_service, "search_contact_by_phone", nunca_cria)

        # Agora a mesma pessoa chega com o número resolvido, carregando o @lid.
        assert await pipe.resolve_contact_id(LOC, FONE, "Luiz", sender_lid=LID) == "C1"

    @pytest.mark.asyncio
    async def test_contato_novo_grava_as_duas_identidades(self, tm, monkeypatch):
        from services import inbound_pipeline as pipe

        async def sem_busca(*a, **kw):
            return None

        async def cria(*a, **kw):
            return {"id": "C9"}

        monkeypatch.setattr(pipe.ghl_service, "search_contact_by_phone", sem_busca)
        monkeypatch.setattr(pipe.ghl_service, "create_contact", cria)

        assert await pipe.resolve_contact_id(LOC, FONE, "Luiz", sender_lid=LID) == "C9"
        # A próxima mensagem pode chegar por qualquer uma das duas pontas.
        assert tm.get_mapped_contact_id(LOC, FONE) == "C9"
        assert tm.get_mapped_contact_id(LOC, LID) == "C9"
