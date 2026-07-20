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
from typing import Optional

import httpx
from fastapi import APIRouter, Header, Path, Request, Response

from auth.token_manager import token_manager
from channels.whatsapp.waha import WAHAChannel, _WAHA_EXT_FALLBACK
from services.channel_policy import WAHA, active_whatsapp_provider
from services.media_store import get_media
from utils.limiter import limiter
from utils.logger import logger
from utils.validators import is_valid_location_id

router = APIRouter(prefix="/media", tags=["Mídia"])

# messageId.ext — letras, números, _, -, um ponto de extensão. Sem barra, sem "..".
_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}\.[A-Za-z0-9]{1,8}$")

_waha = WAHAChannel()


def _waha_filenames(filename: str) -> list[str]:
    """
    Candidatos de nome real no WAHA para um basename público já validado.

    A extensão pública pode ter sido normalizada (`.oga` vira `.ogg` para o player
    do GHL). Aqui revertemos: para `.ogg` tentamos `.ogg` e depois `.oga`. Para
    qualquer outra extensão, só o próprio nome.
    """
    stem, _, ext = filename.rpartition(".")
    alternativas = _WAHA_EXT_FALLBACK.get(ext.lower())
    if not alternativas:
        return [filename]
    return [f"{stem}.{e}" for e in alternativas]


# O player HTML5 do CRM é cross-origin e pede a mídia por partes (Range). Sem
# esses cabeçalhos o <audio> abre mas não toca e a duração fica "--:--".
_BASE_HEADERS = {
    "Accept-Ranges": "bytes",
    "Access-Control-Allow-Origin": "*",
    "Cache-Control": "private, max-age=86400",
}


def _serve(data: bytes, content_type: str, range_header: Optional[str]) -> Response:
    """Devolve o binário inteiro (200) ou um trecho (206) conforme o header Range."""
    total = len(data)
    if range_header:
        trecho = _parse_range(range_header, total)
        if trecho is None:
            return Response(status_code=416, headers={**_BASE_HEADERS, "Content-Range": f"bytes */{total}"})
        inicio, fim = trecho
        corpo = data[inicio:fim + 1]
        headers = {**_BASE_HEADERS, "Content-Range": f"bytes {inicio}-{fim}/{total}"}
        return Response(content=corpo, status_code=206, media_type=content_type, headers=headers)
    return Response(content=data, media_type=content_type, headers=dict(_BASE_HEADERS))


def _parse_range(header: str, total: int) -> Optional[tuple[int, int]]:
    """Interpreta 'bytes=início-fim' (uma faixa). None se inválido/insatisfazível."""
    if not header.startswith("bytes=") or "," in header:
        return None
    spec = header[len("bytes="):].strip()
    ini_s, _, fim_s = spec.partition("-")
    try:
        if ini_s == "":
            # sufixo: os últimos N bytes
            n = int(fim_s)
            if n <= 0:
                return None
            return max(0, total - n), total - 1
        inicio = int(ini_s)
        fim = int(fim_s) if fim_s else total - 1
    except ValueError:
        return None
    if inicio > fim or inicio >= total:
        return None
    return inicio, min(fim, total - 1)


@router.get("/whatsapp/{location_id}/{filename}")
@limiter.limit("240/minute")
async def whatsapp_media(
    request: Request,
    location_id: str = Path(...),
    filename: str = Path(...),
    range: Optional[str] = Header(default=None),
):
    # Público (o GHL busca sem header) e, no cache-miss, faz até 2 GETs ao WAHA
    # interno — sem limite viraria amplificação. 240/min cobre um player abrindo
    # a conversa (vários Range) com folga.
    if not is_valid_location_id(location_id) or not _SAFE_FILENAME.match(filename):
        return Response(status_code=404)

    tenant = token_manager.get_tenant(location_id)
    if not tenant or active_whatsapp_provider(tenant) != WAHA:
        return Response(status_code=404)

    # 1) Store durável primeiro — é o caminho normal: o GHL busca depois que o
    #    WAHA já apagou o arquivo, então quem responde é o nosso banco.
    guardado = await get_media(location_id, filename)
    if guardado:
        data, content_type = guardado
        return _serve(data, content_type, range)

    # 2) Fallback ao vivo no WAHA (mídia recém-chegada que ainda não persistiu, ou
    #    anexo antigo anterior ao store). A extensão pública pode ter sido
    #    normalizada (.oga -> .ogg); tentamos os candidatos reais. A URL é montada
    #    AQUI a partir do tenant + basename validado — o cliente nunca a controla.
    base, key, session = _waha._cfg(tenant)
    if not (base and session):
        return Response(status_code=404)
    headers = {"X-Api-Key": key} if key else {}
    resp = None
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            for candidato in _waha_filenames(filename):
                resp = await client.get(f"{base.rstrip('/')}/api/files/{session}/{candidato}", headers=headers)
                if resp.status_code == 200:
                    break
    except Exception as e:
        logger.error(f"[MEDIA] Falha ao buscar {filename} de {location_id}: {e}")
        return Response(status_code=502)

    if resp is None or resp.status_code != 200:
        code = resp.status_code if resp is not None else 502
        logger.warning(f"[MEDIA] Sem store e WAHA devolveu {code} para {filename} ({location_id}).")
        return Response(status_code=code if code in (404, 410) else 502)

    content_type = (resp.headers.get("content-type") or "application/octet-stream").split(";")[0].strip()
    return _serve(resp.content, content_type, range)
