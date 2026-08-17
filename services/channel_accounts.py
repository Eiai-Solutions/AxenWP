"""
Resolve QUAL conta nossa recebeu a mensagem.

Uma instância passou a poder ter mais de uma conta no mesmo canal (dois números de
WhatsApp, por exemplo). O webhook chega com a identidade da conta na linguagem do
provedor — sessão do WAHA, `instanceId` da Z-API, `@username` do bot — e é aqui que
ela vira o registro em `channel_accounts`.

FALLBACK É REGRA, NÃO CORTESIA. Enquanto `uq_ai_agent_location_channel` existir, há
no máximo uma conta por canal, e quem não souber informar a conta continua sendo
resolvido por `(location_id, channel)` exatamente como antes. É isso que permite
ligar isto sem um big-bang: adapter que ainda não preenche `account_ref` não quebra,
só não ganha a precisão.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from data.database import SessionLocal
from data.models import ChannelAccount
from utils.logger import logger


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
            # Referência que não bate é sinal de config divergente (sessão renomeada
            # no WAHA, instância trocada na Z-API). Não dá para adivinhar de quem é a
            # mensagem, então cai no fallback — mas RUIDOSAMENTE: em silêncio, o
            # sintoma seria a persona errada respondendo, sem nada no log.
            logger.warning(
                f"[CONTA] {location_id}/{channel}: nenhuma conta com external_ref="
                f"{account_ref!r}. Caindo no fallback por canal."
            )

        # Uma conta só no canal: é ela, sem ambiguidade. Duas ou mais sem `account_ref`
        # seria adivinhação — melhor devolver nada e deixar o chamador usar o caminho
        # antigo do que sortear entre dois agentes.
        contas = q.order_by(ChannelAccount.id).limit(2).all()
        return contas[0].id if len(contas) == 1 else None
    finally:
        db.close()


async def resolver(location_id: str, channel: str, account_ref: Optional[str] = None) -> Optional[int]:
    """`channel_account_id` da conta que recebeu, ou None (o chamador usa o fallback)."""
    return await asyncio.to_thread(_resolver_sync, location_id, channel, account_ref)


__all__ = ["resolver"]
