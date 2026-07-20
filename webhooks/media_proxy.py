"""
Proxy de mídia do WhatsApp para o CRM.

O CRM (e o operador) precisa BAIXAR o áudio/imagem/arquivo que o lead mandou. A
mídia do WAHA mora atrás de X-Api-Key e num host interno — o GHL não consegue
buscá-la. Este endpoint público faz a ponte: recebe uma requisição sem
credencial (é o GHL quem chama, server-side), busca o arquivo no WAHA com a
chave, e devolve o binário. O GHL então re-hospeda no CDN dele, igual ao que já
faz com anexos de saída.

Segurança — este endpoint é PÚBLICO (o GHL não manda header):
- `location_id` e `filename` passam por validação estrita de formato;
- `filename` é um BASENAME (sem barra, sem `..`): impossível montar um path para
  `/api/sessions` ou `/api/{s}/chats`;
- a sessão do WAHA vem do TENANT (não do input), então não dá para pedir a mídia
  de outra instância;
- só serve `/api/files/` do WAHA — nenhum outro path é alcançável.
O nome do arquivo é o messageId do WhatsApp (~22 chars aleatórios), então a
exposição é a mesma que a doc do WAHA aceita para `WHATSAPP_API_KEY_EXCLUDE_PATH`,
mas SEM tornar `/api/files` público e SEM expor a chave global.
"""

import re

import httpx
from fastapi import APIRouter, Path, Response

from auth.token_manager import token_manager
from channels.whatsapp.waha import WAHAChannel
from services.channel_policy import WAHA, active_whatsapp_provider
from utils.logger import logger
from utils.validators import is_valid_location_id

router = APIRouter(prefix="/media", tags=["Mídia"])

# messageId.ext — letras, números, _, -, um ponto de extensão. Sem barra, sem "..".
_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}\.[A-Za-z0-9]{1,8}$")

_waha = WAHAChannel()


@router.get("/whatsapp/{location_id}/{filename}")
async def whatsapp_media(
    location_id: str = Path(...),
    filename: str = Path(...),
):
    if not is_valid_location_id(location_id) or not _SAFE_FILENAME.match(filename):
        return Response(status_code=404)

    tenant = token_manager.get_tenant(location_id)
    if not tenant or active_whatsapp_provider(tenant) != WAHA:
        return Response(status_code=404)

    base, key, session = _waha._cfg(tenant)
    if not (base and session):
        return Response(status_code=404)

    # A URL é montada AQUI a partir de dados do tenant + basename validado —
    # o cliente nunca controla o path no WAHA.
    url = f"{base.rstrip('/')}/api/files/{session}/{filename}"
    headers = {"X-Api-Key": key} if key else {}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=headers)
    except Exception as e:
        logger.error(f"[MEDIA] Falha ao buscar {filename} de {location_id}: {e}")
        return Response(status_code=502)

    if resp.status_code != 200:
        # 404 quando o arquivo já expirou no WAHA (retenção curta): o CRM cai no
        # texto descritivo, sem erro visível para o operador.
        logger.warning(f"[MEDIA] WAHA devolveu {resp.status_code} para {filename} ({location_id}).")
        return Response(status_code=resp.status_code if resp.status_code in (404, 410) else 502)

    content_type = resp.headers.get("content-type", "application/octet-stream")
    return Response(
        content=resp.content,
        media_type=content_type,
        headers={"Cache-Control": "private, max-age=3600"},
    )
