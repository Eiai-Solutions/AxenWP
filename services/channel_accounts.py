"""
Resolve QUAL conta nossa recebeu a mensagem.

Uma instância passou a poder ter mais de uma conta no mesmo canal (dois números de
WhatsApp, por exemplo). O webhook chega com a identidade da conta na linguagem do
provedor — sessão do WAHA, `instanceId` da Z-API, `@username` do bot — e é aqui que
ela vira o registro em `channel_accounts`.

ESTE MÓDULO TAMBÉM É O PRODUTOR. A primeira versão só lia — e o único escritor de
`channel_accounts` era o backfill da migration, que roda uma vez. Toda instância
conectada DEPOIS do deploy ficava sem conta nenhuma, e o aviso de "config
divergente" disparava no estado NORMAL, uma vez por turno, para sempre: o alarme
criado para denunciar a persona errada enterrava o próprio sinal. Consumidor sem
produtor é o mesmo cheiro que já apareceu nas portas do wizard.

`sincronizar()` é chamado de todo lugar que grava credencial de canal no tenant.

FALLBACK É REGRA, NÃO CORTESIA. Enquanto `uq_ai_agent_location_channel` existir, há
no máximo uma conta por canal, e quem não souber informar a conta continua sendo
resolvido por `(location_id, channel)` exatamente como antes. É isso que permite
ligar isto sem um big-bang: adapter que ainda não preenche `account_ref` não quebra,
só não ganha a precisão.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from datetime import datetime, timezone

from data.database import SessionLocal
from data.models import ChannelAccount, Tenant
from utils.logger import logger


def _sincronizar_sync(location_id: str, channel: str) -> Optional[int]:
    """
    Cria ou atualiza a conta a partir das colunas planas do tenant.

    Mesma derivação do backfill da migration 034 — de propósito: enquanto houver
    dual-write, as duas fontes têm que contar a mesma história. O provedor de
    WhatsApp é o ATIVO da instância (decisão do dono: a conta herda o provedor),
    então trocar de Z-API para WAHA reescreve a MESMA linha, com a referência nova.
    Sem isso, o `external_ref` apodrece e a conta antiga responde por uma sessão que
    não existe mais.
    """
    db = SessionLocal()
    try:
        t = db.query(Tenant).filter(Tenant.location_id == location_id).first()
        if t is None:
            return None

        if channel == "telegram":
            if not t.telegram_bot_token:
                return None
            dados = {
                "external_ref": (t.telegram_bot_username or None),
                "label": f"Telegram — {t.telegram_bot_username or t.company_name or location_id}"[:120],
                "telegram_bot_token": t.telegram_bot_token,
                "telegram_bot_username": t.telegram_bot_username,
            }
        else:
            provedor = (t.whatsapp_provider or "zapi").lower()
            empresa = (t.company_name or location_id)[:80]
            if provedor == "waha" and (t.waha_base_url or t.waha_session):
                dados = {
                    "external_ref": t.waha_session,
                    "label": f"WhatsApp — {empresa}"[:120],
                    "waha_base_url": t.waha_base_url, "waha_session": t.waha_session,
                    "waha_engine": t.waha_engine, "waha_api_key": t.waha_api_key,
                    # Limpa a credencial do provedor que deixou de valer, senão a
                    # linha guarda duas identidades e a próxima leitura escolhe mal.
                    "zapi_instance_id": None, "zapi_token": None, "zapi_client_token": None,
                }
            elif provedor != "waha" and t.zapi_instance_id:
                dados = {
                    "external_ref": t.zapi_instance_id,
                    "label": f"WhatsApp — {empresa}"[:120],
                    "zapi_instance_id": t.zapi_instance_id, "zapi_token": t.zapi_token,
                    "zapi_client_token": t.zapi_client_token,
                    "waha_base_url": None, "waha_session": None,
                    "waha_engine": None, "waha_api_key": None,
                }
            else:
                return None   # canal sem credencial: não inventa conta

        conta = (
            db.query(ChannelAccount)
            .filter(ChannelAccount.location_id == location_id,
                    ChannelAccount.channel == channel)
            .order_by(ChannelAccount.id)
            .first()
        )
        if conta is None:
            conta = ChannelAccount(location_id=location_id, channel=channel)
            db.add(conta)
        for chave, valor in dados.items():
            setattr(conta, chave, valor)
        conta.updated_at = datetime.now(timezone.utc)
        if conta.created_at is None:
            conta.created_at = conta.updated_at
        db.commit()
        db.refresh(conta)
        logger.info(f"[CONTA] {location_id}/{channel} sincronizada (ref={conta.external_ref!r}).")
        return conta.id
    except Exception as e:
        db.rollback()
        # Sincronizar conta NÃO pode derrubar a conexão do canal: o operador
        # acabou de configurar o WhatsApp e não pode ver erro porque uma tabela
        # que ainda ninguém lê para valer falhou.
        logger.error(f"[CONTA] Falha ao sincronizar {location_id}/{channel}: {type(e).__name__}: {e}")
        return None
    finally:
        db.close()


async def sincronizar(location_id: str, channel: str) -> Optional[int]:
    """Chamado de todo lugar que grava credencial de canal no tenant."""
    return await asyncio.to_thread(_sincronizar_sync, location_id, channel)


def _resolver_sync(location_id: str, channel: str, account_ref: Optional[str]) -> Optional[int]:
    db = SessionLocal()
    try:
        q = db.query(ChannelAccount).filter(
            ChannelAccount.location_id == location_id,
            ChannelAccount.channel == channel,
        )
        if account_ref:
            conta = q.filter(ChannelAccount.external_ref == account_ref).first()
            if conta:
                return conta.id

        # Uma conta só no canal: é ela, sem ambiguidade. Duas ou mais sem `account_ref`
        # seria adivinhação — melhor devolver nada e deixar o chamador usar o caminho
        # antigo do que sortear entre dois agentes.
        contas = q.order_by(ChannelAccount.id).limit(2).all()

        # O aviso só quando há DIVERGÊNCIA de verdade: existem contas e nenhuma
        # bate com a referência que chegou (sessão renomeada no WAHA, instância
        # trocada na Z-API). "Nenhuma conta ainda" é estado normal de instância
        # nova e não pode virar WARNING por turno — foi assim que este alarme
        # começou, enterrando o próprio sinal que existe para dar.
        if account_ref and contas:
            logger.warning(
                f"[CONTA] {location_id}/{channel}: chegou external_ref={account_ref!r} "
                f"e nenhuma das {len(contas)} conta(s) bate. Caindo no fallback por canal."
            )

        return contas[0].id if len(contas) == 1 else None
    finally:
        db.close()


async def resolver(location_id: str, channel: str, account_ref: Optional[str] = None) -> Optional[int]:
    """`channel_account_id` da conta que recebeu, ou None (o chamador usa o fallback)."""
    return await asyncio.to_thread(_resolver_sync, location_id, channel, account_ref)


__all__ = ["resolver", "sincronizar"]
