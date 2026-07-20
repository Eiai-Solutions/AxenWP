"""
Gerenciamento de tokens OAuth do GoHighLevel usando PostgreSQL (SQLAlchemy).
Renova tokens automaticamente quando expiram.
"""

import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from utils.logger import logger
from utils.config import settings
from data.database import SessionLocal
from data.models import Tenant

class TokenManager:
    """Gerencia tokens de todos os tenants armazenados no bando de dados."""

    # Refresh que acabou de falhar não é retentado a cada mensagem: sem isso, um
    # tenant com OAuth morto e PIT vivo pagaria um round-trip perdido por
    # chamada, no caminho quente do webhook. Ao expirar a janela, tenta de novo —
    # se o operador reinstalar o app, voltamos ao OAuth sozinhos.
    _REFRESH_COOLDOWN_SEGUNDOS = 300.0

    def __init__(self):
        self._refresh_falhou_em: dict[str, float] = {}

    def get_tenant(self, location_id: str, db: Session = None) -> Optional[Tenant]:
        """Retorna o tenant pelo location_id a partir do banco de dados."""
        session = db or SessionLocal()
        try:
            return session.query(Tenant).filter(Tenant.location_id == location_id).first()
        finally:
            if not db:
                session.close()

    def get_all_tenants(self, db: Session = None) -> list[Tenant]:
        """Retorna todos os tenants cadastrados no banco."""
        session = db or SessionLocal()
        try:
            return session.query(Tenant).all()
        finally:
            if not db:
                session.close()

    def register_tenant(
        self,
        location_id: str,
        company_name: str,
        access_token: str,
        refresh_token: str,
        expires_in: int,
        client_id: str = "",
        client_secret: str = "",
        **extras,
    ) -> Tenant:
        """Registra (ou atualiza) um tenant com os dados do OAuth no DB."""
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

        db = SessionLocal()
        try:
            tenant = self.get_tenant(location_id, db=db)
            if not tenant:
                tenant = Tenant(location_id=location_id)
                db.add(tenant)

            tenant.company_name = company_name or tenant.company_name
            tenant.client_id = client_id or tenant.client_id or settings.ghl_client_id
            tenant.client_secret = client_secret or tenant.client_secret or settings.ghl_client_secret
            tenant.access_token = access_token
            tenant.refresh_token = refresh_token
            tenant.token_expires_at = expires_at.isoformat()
            
            # Atualiza extras se existirem
            for key, value in extras.items():
                if hasattr(tenant, key):
                    setattr(tenant, key, value)

            db.commit()
            db.refresh(tenant)
            
            logger.info(f"Tenant {tenant.company_name} ({tenant.location_id}) registrado no banco")
            return tenant
        finally:
            db.close()
            
    def link_ghl_to_existing_tenant(
        self,
        existing_location_id: str,
        ghl_location_id: str,
        access_token: str,
        refresh_token: str,
        expires_in: int,
        client_id: str = "",
        client_secret: str = "",
    ) -> Optional[Tenant]:
        """Vincula credenciais GHL a um tenant existente (whatsapp_only → CRM para qualificação)."""
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

        db = SessionLocal()
        try:
            tenant = self.get_tenant(existing_location_id, db=db)
            if not tenant:
                logger.error(f"Tenant {existing_location_id} não encontrado para vincular CRM.")
                return None

            tenant.ghl_location_id = ghl_location_id
            tenant.client_id = client_id or tenant.client_id
            tenant.client_secret = client_secret or tenant.client_secret
            tenant.access_token = access_token
            tenant.refresh_token = refresh_token
            tenant.token_expires_at = expires_at.isoformat()

            db.commit()
            db.refresh(tenant)

            logger.info(f"CRM vinculado ao tenant {tenant.company_name} ({existing_location_id}), ghl_location_id={ghl_location_id}")
            return tenant
        finally:
            db.close()

    def update_zapi_credentials(self, location_id: str, instance_id: str, token: str, client_token: str = ""):
        """Atualiza as credenciais Z-API de um determinado tenant."""
        db = SessionLocal()
        try:
            tenant = self.get_tenant(location_id, db=db)
            if tenant:
                tenant.zapi_instance_id = instance_id
                tenant.zapi_token = token
                tenant.zapi_client_token = client_token
                db.commit()
                logger.info(f"Credenciais Z-API salvas no banco para {tenant.company_name}")
        finally:
            db.close()

    def create_whatsapp_tenant(
        self,
        company_name: str,
        zapi_instance_id: str,
        zapi_token: str,
        zapi_client_token: str = "",
    ) -> Tenant:
        """Cria um tenant WhatsApp-only (sem GHL/CRM)."""
        location_id = f"wp_{uuid.uuid4().hex[:12]}"
        db = SessionLocal()
        try:
            tenant = Tenant(
                location_id=location_id,
                company_name=company_name,
                mode="whatsapp_only",
                zapi_instance_id=zapi_instance_id,
                zapi_token=zapi_token,
                zapi_client_token=zapi_client_token,
                is_active=True,
            )
            db.add(tenant)
            db.commit()
            db.refresh(tenant)
            logger.info(f"Tenant WhatsApp-only '{company_name}' ({location_id}) criado.")
            return tenant
        finally:
            db.close()

    def create_lite_tenant(self, company_name: str) -> Tenant:
        """Cria um tenant 'lite' — só onboarding, sem CRM nem Z-API.

        Já gera o form_token para o link público /form/{token} ser entregue
        imediatamente. Canais (Z-API, Telegram) e CRM são adicionados depois
        pelo admin.
        """
        location_id = f"ob_{uuid.uuid4().hex[:12]}"
        db = SessionLocal()
        try:
            tenant = Tenant(
                location_id=location_id,
                company_name=company_name,
                mode="lite",
                form_token=uuid.uuid4().hex,
                is_active=True,
            )
            db.add(tenant)
            db.commit()
            db.refresh(tenant)
            logger.info(f"Tenant lite '{company_name}' ({location_id}) criado.")
            return tenant
        finally:
            db.close()

    def delete_tenant(self, location_id: str) -> bool:
        """Deleta um tenant e todos os dados relacionados (cascade)."""
        db = SessionLocal()
        try:
            tenant = self.get_tenant(location_id, db=db)
            if not tenant:
                return False
            db.delete(tenant)
            db.commit()
            logger.info(f"Tenant {location_id} deletado com sucesso.")
            return True
        except Exception as e:
            logger.error(f"Erro ao deletar tenant {location_id}: {e}")
            db.rollback()
            return False
        finally:
            db.close()

    def toggle_active_status(self, location_id: str, is_active: bool):
        """Ativa/desativa a automação inteira no nível do Tenant."""
        db = SessionLocal()
        try:
            tenant = self.get_tenant(location_id, db=db)
            if tenant:
                tenant.is_active = is_active
                db.commit()
                logger.info(f"Automação de {tenant.company_name} alterada para {'ATIVA' if is_active else 'PAUSADA'}")
        finally:
            db.close()

    def is_token_expired(self, tenant: Tenant) -> bool:
        """Verifica se o access_token está expirado ou prestes a expirar."""
        if not tenant.token_expires_at:
            return True
        try:
            expires = datetime.fromisoformat(tenant.token_expires_at.replace("Z", "+00:00"))
            margin = timedelta(hours=1)
            return datetime.now(timezone.utc) >= (expires - margin)
        except (ValueError, TypeError):
            return True

    def _em_cooldown(self, location_id: str) -> bool:
        falhou_em = self._refresh_falhou_em.get(location_id)
        if falhou_em is None:
            return False
        return (time.monotonic() - falhou_em) < self._REFRESH_COOLDOWN_SEGUNDOS

    def _has_oauth(self, tenant) -> bool:
        return bool(getattr(tenant, "access_token", None) or getattr(tenant, "refresh_token", None))

    async def get_valid_token(self, location_id: str) -> Optional[str]:
        """
        Token válido para chamadas à API do GHL.

        O token do APP (OAuth) tem prioridade sobre o PIT quando existe. Não é
        preferência estética: operações ligadas a conversation provider — em
        especial o `PUT /conversations/messages/{id}/status` — são recusadas com
        401 `CONVERSATIONS_MSG_PROVIDER_NO_ACCESS` para qualquer token que não
        pertença ao app dono do provider. Com o PIT na frente, todo status de
        entrega que reportamos ao CRM falhava calado.

        O PIT continua sendo o token de quem nunca instalou o app, e vira
        fallback se o OAuth não puder ser renovado — perder o acesso inteiro
        seria pior do que perder só o status.
        """
        tenant = self.get_tenant(location_id)
        if not tenant:
            logger.error(f"Tenant {location_id} não encontrado")
            return None

        if self._has_oauth(tenant):
            if not self.is_token_expired(tenant):
                return tenant.access_token

            if tenant.pit_token and self._em_cooldown(location_id):
                return tenant.pit_token

            logger.info(f"Token expirado para {tenant.company_name}, renovando...")
            if await self._refresh_token(tenant.location_id):
                self._refresh_falhou_em.pop(location_id, None)
                return self.get_tenant(location_id).access_token

            self._refresh_falhou_em[location_id] = time.monotonic()
            if tenant.pit_token:
                logger.warning(
                    f"Refresh OAuth falhou para {tenant.company_name}; usando PIT como fallback "
                    f"(status de entrega no CRM não vai subir enquanto isso durar)."
                )
                return tenant.pit_token

            logger.error(f"Falha ao renovar token para {tenant.company_name}")
            return None

        if tenant.pit_token:
            return tenant.pit_token

        return None

    async def _refresh_token(self, location_id: str) -> bool:
        """Faz o refresh do access_token via API GHL."""
        db = SessionLocal()
        try:
            tenant = self.get_tenant(location_id, db=db)
            if not tenant:
                return False

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{settings.ghl_api_base}/oauth/token",
                    data={
                        "client_id": tenant.client_id,
                        "client_secret": tenant.client_secret,
                        "grant_type": "refresh_token",
                        "refresh_token": tenant.refresh_token,
                        "user_type": "Location",
                        "redirect_uri": settings.ghl_redirect_uri,
                    },
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                )

                if response.status_code != 200:
                    logger.error(
                        f"Refresh falhou para {tenant.company_name}: "
                        f"status={response.status_code}, body={response.text}"
                    )
                    return False

                data = response.json()
                expires_at = datetime.now(timezone.utc) + timedelta(
                    seconds=data.get("expires_in", 86399)
                )

                tenant.access_token = data["access_token"]
                tenant.refresh_token = data["refresh_token"]
                tenant.token_expires_at = expires_at.isoformat()

                db.commit()

                logger.info(f"Token renovado com sucesso para {tenant.company_name}")
                return True

        except Exception as e:
            logger.error(f"Exceção ao renovar token: {e}")
            return False
        finally:
            db.close()

    async def refresh_all_tokens(self):
        """Verifica e renova tokens de todos os tenants que estão prestes a expirar."""
        logger.info("Verificando tokens de todos os tenants no banco...")
        db = SessionLocal()
        try:
            tenants = db.query(Tenant).all()
            for tenant in tenants:
                # Quem tem OAuth precisa de refresh mesmo tendo PIT: desde que o
                # token do app passou a ter prioridade, deixar o OAuth expirar
                # rebaixaria o tenant para o PIT e derrubaria o status de entrega.
                if not self._has_oauth(tenant):
                    continue
                if self.is_token_expired(tenant):
                    await self._refresh_token(tenant.location_id)
        finally:
            db.close()

    def register_pit_tenant(
        self,
        company_name: str,
        pit_token: str,
        location_id: str,
    ) -> Tenant:
        """Registra um tenant usando Private Integration Token (sem OAuth)."""
        db = SessionLocal()
        try:
            tenant = self.get_tenant(location_id, db=db)
            if not tenant:
                tenant = Tenant(location_id=location_id)
                db.add(tenant)

            tenant.company_name = company_name
            tenant.pit_token = pit_token
            tenant.mode = "ghl"
            tenant.is_active = True

            db.commit()
            db.refresh(tenant)

            logger.info(f"Tenant PIT '{company_name}' ({location_id}) registrado.")
            return tenant
        finally:
            db.close()

    def update_pit_token(self, location_id: str, pit_token: str) -> bool:
        """Atualiza ou adiciona PIT a um tenant existente."""
        db = SessionLocal()
        try:
            tenant = self.get_tenant(location_id, db=db)
            if not tenant:
                return False
            tenant.pit_token = pit_token
            db.commit()
            logger.info(f"PIT atualizado para tenant {tenant.company_name}")
            return True
        finally:
            db.close()

    # --- CONTACT MAPPING FUNCS ---
    def get_mapped_contact_id(self, location_id: str, phone_or_lid: str) -> Optional[str]:
        """
        Contato do GHL por QUALQUER uma das identidades da pessoa.

        A mesma pessoa chega ora como telefone, ora como @lid (o WhatsApp nem
        sempre entrega o número). Procurar só pela chave recebida faria nascer um
        contato duplicado toda vez que ela aparecesse pela outra ponta.
        """
        from data.models import ContactMapping
        db = SessionLocal()
        try:
            mapping = db.query(ContactMapping).filter_by(
                location_id=location_id,
                phone_or_lid=phone_or_lid
            ).first()
            if not mapping:
                mapping = db.query(ContactMapping).filter_by(
                    location_id=location_id, lid=phone_or_lid
                ).first()
            return mapping.ghl_contact_id if mapping else None
        finally:
            db.close()

    def get_phone_by_lid(self, location_id: str, lid: str) -> Optional[str]:
        """
        Telefone já conhecido de um @lid, se algum dia resolvemos essa pessoa.

        É a última camada de resolução de identidade: quando o payload não traz o
        número e o servidor do provedor não responde, ainda conseguimos responder
        a quem já conversou com a gente antes.
        """
        from data.models import ContactMapping
        db = SessionLocal()
        try:
            mapping = db.query(ContactMapping).filter_by(
                location_id=location_id, lid=lid
            ).first()
            if mapping and mapping.phone_or_lid and "@" not in mapping.phone_or_lid:
                return mapping.phone_or_lid
            return None
        finally:
            db.close()

    def save_contact_mapping(
        self, location_id: str, phone_or_lid: str, ghl_contact_id: str, lid: Optional[str] = None
    ):
        """
        Associa a identidade do WhatsApp ao contato do GHL.

        Quando as duas identidades são conhecidas (telefone resolvido a partir de
        um @lid), gravamos ambas na MESMA linha — é isso que impede a duplicata
        quando a pessoa reaparece pela outra identidade.
        """
        from data.models import ContactMapping
        db = SessionLocal()
        try:
            mapping_id = f"{location_id}_{phone_or_lid}"
            mapping = db.query(ContactMapping).filter_by(id=mapping_id).first()
            if not mapping:
                mapping = ContactMapping(
                    id=mapping_id,
                    location_id=location_id,
                    phone_or_lid=phone_or_lid,
                    ghl_contact_id=ghl_contact_id,
                    lid=lid,
                )
                db.add(mapping)
            else:
                mapping.ghl_contact_id = ghl_contact_id
                # Só preenche; nunca apaga um lid já conhecido com None.
                if lid:
                    mapping.lid = lid
            db.commit()
        except Exception as e:
            logger.error(f"Erro ao salvar mapping de contato DB: {e}")
        finally:
            db.close()

    def delete_contact_mapping(self, location_id: str, phone_or_lid: str):
        """
        Remove o mapeamento (contato deletado no GHL).

        Apaga por telefone E por lid: deixar a linha alias sobreviver apontando
        para um contato morto faria a próxima mensagem reusar um contact_id que
        não existe mais, em loop e sem auto-cura.
        """
        from data.models import ContactMapping
        db = SessionLocal()
        try:
            db.query(ContactMapping).filter_by(
                id=f"{location_id}_{phone_or_lid}"
            ).delete()
            db.query(ContactMapping).filter_by(
                location_id=location_id, lid=phone_or_lid
            ).delete()
            db.commit()
        except Exception as e:
            logger.error(f"Erro ao deletar mapping de contato DB: {e}")
        finally:
            db.close()

    # --- MESSAGE MAPPING FUNCS ---
    def save_message_mapping(self, zapi_message_id: str, ghl_message_id: str, location_id: str):
        """Salva a associação entre o ID da mensagem na Z-API e no GHL."""
        from data.models import MessageMapping
        db = SessionLocal()
        try:
            mapping = db.query(MessageMapping).filter_by(zapi_message_id=zapi_message_id).first()
            if not mapping:
                mapping = MessageMapping(
                    zapi_message_id=zapi_message_id,
                    ghl_message_id=ghl_message_id,
                    location_id=location_id
                )
                db.add(mapping)
            else:
                mapping.ghl_message_id = ghl_message_id
            db.commit()
        except Exception as e:
            logger.error(f"Erro ao salvar mapping de mensagem DB: {e}")
        finally:
            db.close()

    def get_ghl_message_id_by_zapi(self, zapi_message_id: str) -> Optional[dict]:
        """Tenta achar o ID da mensagem no GHL pelo ID da Z-API."""
        from data.models import MessageMapping
        db = SessionLocal()
        try:
            mapping = db.query(MessageMapping).filter_by(zapi_message_id=zapi_message_id).first()
            if mapping:
                return {
                    "ghl_message_id": mapping.ghl_message_id,
                    "location_id": mapping.location_id
                }
            return None
        finally:
            db.close()

# Instância global
token_manager = TokenManager()
