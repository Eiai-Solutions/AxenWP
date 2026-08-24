"""
Autenticação de MÁQUINA: a chave que um CRM de terceiro usa para falar com o hub.

Até aqui só existia autenticação de humano — cookie `admin_session`, sessão de
navegador, `require_admin`. Um CRM não tem navegador nem sessão; sem isto, "o
controle da IA é do CRM" não tinha por onde entrar.

Duas propriedades que o resto do sistema herda daqui:

  · **O tenant vem da CHAVE, não da URL.** Nenhuma rota precisa (nem pode) receber
    `location_id` no caminho para saber de quem é a requisição. Uma chave do
    tenant A não consegue tocar no tenant B nem por bug de rota nova.
  · **A chave nunca é comparada, só procurada.** Guardamos o SHA-256 e buscamos
    por ele. Não há comparação de segredo para vazar por tempo, e não há KDF caro
    no caminho quente.

Por que SHA-256 e não o `scrypt` do login: senha de gente tem pouca entropia e
precisa de KDF lento contra dicionário. Uma chave nossa tem 256 bits sorteados —
não há dicionário — e um scrypt por requisição de API custaria caro à toa.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Header, HTTPException

from utils.logger import logger

# `mc` = MilloChat, `live` deixa espaço para uma chave de teste no futuro. O prefixo
# também é o que torna a chave reconhecível num log ou num repositório vazado —
# scanners de segredo procuram padrões assim.
PREFIXO = "mc_live_"

# 32 bytes = 256 bits. `token_urlsafe` já entrega base64-url sem padding.
BYTES_DE_ENTROPIA = 32

# `last_used_at` não é auditoria fina — é "essa integração está viva?". Gravar a
# cada request seria uma escrita por chamada de API só para mexer num timestamp.
JANELA_DE_USO = timedelta(minutes=5)


def gerar() -> tuple[str, str, str]:
    """
    Cria uma chave nova. Devolve (chave_em_claro, prefixo_visivel, hash).

    A chave em claro só existe aqui e na resposta ao operador. Não é gravada.
    """
    segredo = secrets.token_urlsafe(BYTES_DE_ENTROPIA)
    chave = f"{PREFIXO}{segredo}"
    return chave, chave[:16], _hash(chave)


def _hash(chave: str) -> str:
    return hashlib.sha256(chave.encode("utf-8")).hexdigest()


def _extrair(authorization: Optional[str], x_api_key: Optional[str]) -> Optional[str]:
    """
    Aceita `Authorization: Bearer <chave>` ou `X-API-Key: <chave>`.

    Os dois porque a realidade é essa: um n8n manda header customizado sem pensar,
    um cliente de HTTP decente manda Bearer. Recusar um dos dois só gera chamado de
    suporte — o segredo é o mesmo.
    """
    if authorization:
        partes = authorization.split(None, 1)
        if len(partes) == 2 and partes[0].lower() == "bearer":
            return partes[1].strip()
    if x_api_key:
        return x_api_key.strip()
    return None


def resolver_sync(chave: str) -> Optional[str]:
    """
    `location_id` do dono da chave, ou None. Sync — chamar via `asyncio.to_thread`.

    None cobre TODOS os casos de recusa (chave inexistente, revogada, tenant
    inativo) de propósito: quem chama devolve o mesmo 401 para todos, e assim a
    resposta não vira um oráculo dizendo quais chaves existem.
    """
    from data.database import SessionLocal
    from data.models import Tenant, TenantApiKey

    db = SessionLocal()
    try:
        linha = (
            db.query(TenantApiKey)
            .filter(TenantApiKey.key_hash == _hash(chave))
            .first()
        )
        if linha is None or linha.revoked_at is not None:
            return None

        tenant = db.query(Tenant).filter(Tenant.location_id == linha.location_id).first()
        if tenant is None or not getattr(tenant, "is_active", True):
            logger.warning(
                f"[API] Chave {linha.prefixo}… de tenant inativo/ausente ({linha.location_id})."
            )
            return None

        agora = datetime.utcnow()
        if linha.last_used_at is None or (agora - linha.last_used_at) > JANELA_DE_USO:
            linha.last_used_at = agora
            db.commit()

        return linha.location_id
    except Exception as e:
        # Nunca deixa a exceção subir com a chave junto. E falha FECHADO: se não dá
        # para verificar, não dá para autorizar.
        logger.error(f"[API] Falha ao resolver chave: {type(e).__name__}: {e}")
        return None
    finally:
        db.close()


async def tenant_da_chave(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None),
) -> str:
    """
    Dependência do FastAPI. Devolve o `location_id` dono da chave, ou levanta 401.

    Use como `location_id: str = Depends(tenant_da_chave)`. Uma rota que recebe o
    `location_id` pelo caminho E confia nele está errada — o caminho é do cliente,
    a chave é nossa.
    """
    import asyncio

    chave = _extrair(authorization, x_api_key)
    if not chave:
        raise HTTPException(
            status_code=401,
            detail="Informe a chave em 'Authorization: Bearer <chave>' ou 'X-API-Key'.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    location_id = await asyncio.to_thread(resolver_sync, chave)
    if not location_id:
        # Mensagem única para chave inexistente, revogada e tenant inativo: a
        # diferença entre elas é informação que só serve para quem está tentando.
        raise HTTPException(
            status_code=401,
            detail="Chave inválida ou revogada.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return location_id


def chave_do_rate_limit(request) -> str:
    """
    Limita por CHAVE quando há chave, e por IP quando não há.

    Sem isto o limite seria por IP, e vários clientes atrás do mesmo NAT de nuvem
    se derrubariam. O valor devolvido é um HASH: a chave não entra no armazenamento
    do limiter (nem em memória, nem em Redis se um dia virar).
    """
    from slowapi.util import get_remote_address

    chave = _extrair(
        request.headers.get("authorization"), request.headers.get("x-api-key")
    )
    if chave:
        return "k:" + hashlib.sha256(chave.encode("utf-8")).hexdigest()[:32]
    return get_remote_address(request)
