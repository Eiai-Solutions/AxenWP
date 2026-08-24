"""
Gestão de conexões WhatsApp (WAHA) pelo painel.

O painel do WAHA fica invisível: criar/conectar/desconectar número acontece no
admin do MilloChat. Config do servidor WAHA é global (um servidor compartilhado);
cada tenant vira uma sessão nomeada pelo seu location_id.

Fluxo de conexão:
  connect -> cria/inicia sessão + registra webhook -> status vira SCAN_QR_CODE
  -> painel mostra o QR (get_qr) -> usuário escaneia -> status vira WORKING.
"""

from fastapi import APIRouter, Depends, Form, Response
from typing import Optional

from admin.dashboard import require_admin, verify_admin
from auth.token_manager import token_manager
from data.database import SessionLocal
from data.models import SystemSettings, Tenant
from services.channel_policy import WAHA, conflict_message, provider_with_article, whatsapp_conflict
from services.waha_service import get_global_waha_config, invalidate_global_waha_config, waha_service
from utils.config import settings as app_settings
from utils.logger import logger

router = APIRouter(prefix="/admin/waha", tags=["WAHA Conexões"])


def _mask(secret: Optional[str]) -> str:
    if not secret:
        return ""
    if len(secret) <= 8:
        return "••••"
    return f"{secret[:4]}…{secret[-4:]}"


def _global_cfg() -> tuple[Optional[str], Optional[str]]:
    """Config global do servidor WAHA (uma vez, para todos os tenants)."""
    return get_global_waha_config(force=True)


def _resolve(tenant) -> tuple[Optional[str], Optional[str], str]:
    """Config efetiva do tenant: override por-tenant senão o global. Session = location_id."""
    g_url, g_key = _global_cfg()
    base = getattr(tenant, "waha_base_url", None) or g_url
    key = getattr(tenant, "waha_api_key", None) or g_key
    session = getattr(tenant, "waha_session", None) or tenant.location_id
    return base, key, session


# ── Config global do servidor WAHA ──

@router.get("/settings")
async def get_waha_settings(authenticated: bool = Depends(verify_admin)):
    if not authenticated:
        return {"error": "Unauthorized"}
    url, key = _global_cfg()
    return {"configured": bool(url and key), "url": url or "", "api_key_masked": _mask(key)}


@router.post("/settings")
async def save_waha_settings(
    admin_waha_url: str = Form(""),
    admin_waha_api_key: str = Form(""),
    authenticated: bool = Depends(verify_admin),
):
    if not authenticated:
        return {"error": "Unauthorized"}
    db = SessionLocal()
    try:
        s = db.query(SystemSettings).first()
        if not s:
            s = SystemSettings()
            db.add(s)
        s.admin_waha_url = admin_waha_url.strip().rstrip("/") or None
        # Só sobrescreve a key se veio preenchida (permite editar url sem reenviar a key).
        if admin_waha_api_key.strip():
            s.admin_waha_api_key = admin_waha_api_key.strip()
        db.commit()
        invalidate_global_waha_config()
        return {"success": True}
    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao salvar WAHA settings: {e}")
        return {"error": str(e)}
    finally:
        db.close()


@router.post("/settings/test")
async def test_waha_connection(authenticated: bool = Depends(verify_admin)):
    """Ping no servidor WAHA (lista sessões) para validar url+api_key."""
    if not authenticated:
        return {"error": "Unauthorized"}
    url, key = _global_cfg()
    if not (url and key):
        return {"ok": False, "error": "WAHA não configurado"}
    sessions = await waha_service.list_sessions(url, key)
    if sessions is None:
        return {"ok": False, "error": "Falha ao conectar (verifique url/api key)"}
    return {"ok": True, "sessions_count": len(sessions)}


# ── Conexão por tenant ──

@router.get("/tenant/{location_id}/status")
async def waha_status(location_id: str, authenticated: bool = Depends(verify_admin)):
    if not authenticated:
        return {"error": "Unauthorized"}
    tenant = token_manager.get_tenant(location_id)
    if not tenant:
        return {"error": "Tenant não encontrado"}
    base, key, session = _resolve(tenant)
    if not (base and key):
        return {"configured": False}
    info = await waha_service.get_session(base, key, session)
    status = (info or {}).get("status", "STOPPED") if info else "UNKNOWN"
    result = {"configured": True, "session": session, "status": status,
              "provider": getattr(tenant, "whatsapp_provider", "zapi")}
    if status == "WORKING":
        me = await waha_service.get_me(base, key, session)
        if me:
            result["me"] = me.get("id") or me.get("pushName") or me
    return result


@router.post("/tenant/{location_id}/connect")
async def waha_connect(location_id: str, force: bool = False, authenticated: bool = Depends(verify_admin)):
    """
    Cria/inicia a sessão do tenant, registra o webhook e marca o tenant como WAHA.

    Recusa se a instância já tem outro provedor de WhatsApp ativo — `force=1` é a
    troca deliberada, feita pelo operador depois do aviso. A troca NÃO apaga as
    credenciais da Z-API: elas ficam dormentes e voltam a valer se ele
    desconectar o WAHA.
    """
    if not authenticated:
        return {"error": "Unauthorized"}
    tenant = token_manager.get_tenant(location_id)
    if not tenant:
        return {"error": "Tenant não encontrado"}

    blocking = whatsapp_conflict(tenant, WAHA)
    if blocking and not force:
        return {
            "conflict": blocking,
            "error": conflict_message(blocking, WAHA),
            "swap_hint": f"Conectar pelo WAHA desativa {provider_with_article(blocking)} desta instância.",
        }

    base, key, session = _resolve(tenant)
    if not (base and key):
        return {"error": "Configure o servidor WAHA (URL + API key) primeiro."}

    public_base = (app_settings.public_base_url or "").rstrip("/")
    if not public_base:
        # Recusa em vez de seguir com `webhook_url=None`. Sem URL, o
        # `create_session` não monta o bloco de config e NUNCA registra webhook
        # nenhum — nem endereço, nem HMAC — e a rota ainda respondia
        # `{"success": True}`. O operador via "Conectado", virava o
        # `WEBHOOK_AUTH_MODE` para `enforce` no deploy seguinte e só então
        # descobria que assinatura nenhuma tinha sido registrada, com o
        # atendimento já bloqueado. É o modo de falha mais provável da própria
        # operação de fechar a porta.
        return {"error": "Configure PUBLIC_BASE_URL antes de conectar — sem ela o "
                         "webhook (e a assinatura) não são registrados."}
    webhook_url = f"{public_base}/webhook/whatsapp/{location_id}"
    hmac_key = (getattr(app_settings, "waha_webhook_hmac_key", "") or "").strip() or None

    created = await waha_service.create_session(
        base, key, session, webhook_url=webhook_url,
        events=["message", "session.status"], hmac_key=hmac_key, start=True,
    )
    if not created:
        # Servidor fora do ar / recusou: NÃO marcar a instância como WAHA. Marcar
        # aqui derrubaria o provedor anterior e deixaria a instância sem WhatsApp
        # nenhum — pior que não ter trocado.
        logger.error(f"[CHANNEL] connect: sessão WAHA não foi criada para {location_id}; provedor mantido.")
        return {"error": "Não foi possível criar a sessão no servidor WAHA. Nada foi alterado."}

    # O tenant guarda APENAS a sua sessão (o número) + o provedor.
    # A URL/API key do servidor WAHA sao config GLOBAL do admin (uma vez, para todos)
    # e sao resolvidas no uso — assim trocar o servidor global vale para todo mundo,
    # sem copias velhas espalhadas por tenant.
    sincronizou = False
    db = SessionLocal()
    try:
        t = db.query(Tenant).filter(Tenant.location_id == location_id).first()
        if t:
            if blocking:
                logger.info(f"[CHANNEL] Troca de provedor {blocking} -> waha em {location_id}")
            t.whatsapp_provider = "waha"
            t.waha_session = session
            db.commit()
            sincronizou = True
        else:
            logger.error(f"[CHANNEL] connect: tenant {location_id} sumiu antes de gravar o provedor")
    finally:
        db.close()

    if sincronizou:
        # Sem isto a linha em `channel_accounts` só existiria para quem já estava
        # configurado quando a migration 034 rodou: instância nova ficaria sem conta,
        # e o roteamento por conta cairia no fallback para sempre.
        from services.channel_accounts import sincronizar as _sincronizar_conta
        await _sincronizar_conta(location_id, "whatsapp")


    info = await waha_service.get_session(base, key, session)
    return {"success": True, "session": session, "status": (info or {}).get("status", "STARTING")}


@router.post("/tenant/{location_id}/reregistrar-webhook")
async def waha_reregistrar_webhook(location_id: str, _: bool = Depends(require_admin)):
    """
    Re-registra SÓ o webhook da sessão, com a chave HMAC atual.

    `require_admin` e não `verify_admin`: o segundo só INFORMA (devolve bool) e
    depende de cada handler lembrar de checar. Esta rota nasceu sem o
    `if not authenticated` que os vizinhos têm e, com isso, um cookie de CLIENTE
    re-registrava o webhook de qualquer tenant — o inventário de
    `tests/test_barreira_operador.py` pegou. `require_admin` levanta 403 antes de
    o corpo rodar, então esquecer a checagem deixa de ser possível.

    É a operação do rollout de assinatura. `connect` também atualizaria o webhook,
    mas ele faz mais: garante sessão iniciada e, em certos estados, leva a um novo
    QR. Numa instância atendendo agora, "mais" é risco.

    **Não é de graça:** aplicar a config faz o WAHA reciclar a sessão — medido em
    produção em 2026-08-24, o status foi de `WORKING` para `STARTING` e voltou em
    segundos, sem novo QR. É uma janela curta, não zero. Não rode isto no pico.

    Depois disto o WAHA passa a assinar. Confira em `GET /admin/health` que
    `millochat_webhook_assinatura_total{resultado="ok"}` sobe e o `falhou` para,
    e só então vire `WEBHOOK_AUTH_MODE` para `enforce`.
    """
    tenant = token_manager.get_tenant(location_id)
    if not tenant:
        return {"error": "Instância não encontrada."}

    base, key, session = _resolve(tenant)
    if not (base and key and session):
        return {"error": "Configure o servidor WAHA (URL + API key) e a sessão primeiro."}

    public_base = (app_settings.public_base_url or "").rstrip("/")
    if not public_base:
        return {"error": "PUBLIC_BASE_URL não configurada — sem ela não há URL para registrar."}

    hmac_key = (getattr(app_settings, "waha_webhook_hmac_key", "") or "").strip() or None
    ok = await waha_service.set_session_webhook(
        base, key, session,
        f"{public_base}/webhook/whatsapp/{location_id}",
        ["message", "session.status"],
        hmac_key=hmac_key,
    )
    if not ok:
        return {"error": "O servidor WAHA recusou a atualização do webhook."}

    logger.info(
        f"[WAHA] Webhook re-registrado para {location_id} "
        f"({'COM' if hmac_key else 'SEM'} assinatura)."
    )
    return {
        "success": True,
        "assinatura": bool(hmac_key),
        "aviso": (
            "Sem WAHA_WEBHOOK_HMAC_KEY no ambiente, o webhook foi registrado SEM "
            "assinatura — o canal segue aberto."
        ) if not hmac_key else (
            "O WAHA passa a assinar. Confira em /admin/health que as assinaturas "
            "estão dando 'ok' antes de virar WEBHOOK_AUTH_MODE para 'enforce'."
        ),
    }


@router.get("/tenant/{location_id}/qr")
async def waha_qr(location_id: str, authenticated: bool = Depends(verify_admin)):
    if not authenticated:
        return Response(status_code=401)
    tenant = token_manager.get_tenant(location_id)
    if not tenant:
        return Response(status_code=404)
    base, key, session = _resolve(tenant)
    if not (base and key):
        return Response(status_code=400)
    qr = await waha_service.get_qr(base, key, session)
    if not qr:
        return Response(status_code=404)
    content, content_type = qr
    return Response(content=content, media_type=content_type)


@router.post("/tenant/{location_id}/{action}")
async def waha_session_action(location_id: str, action: str, authenticated: bool = Depends(verify_admin)):
    """restart | logout | disconnect (logout+delete da sessão)."""
    if not authenticated:
        return {"error": "Unauthorized"}
    if action not in ("restart", "logout", "disconnect"):
        return {"error": "Ação inválida"}
    tenant = token_manager.get_tenant(location_id)
    if not tenant:
        return {"error": "Tenant não encontrado"}
    base, key, session = _resolve(tenant)
    if not (base and key):
        # Sem servidor global não há o que desligar lá fora — mas o desconectar
        # precisa continuar sendo a saída do operador, senão a instância fica
        # presa em WAHA (sem canal e sem poder voltar) só porque o admin trocou
        # a configuração global.
        if action == "disconnect":
            _release_whatsapp_provider(location_id)
            return {"success": True, "note": "Servidor WAHA não configurado; provedor liberado localmente."}
        return {"error": "WAHA não configurado"}

    if action == "restart":
        ok = await waha_service.restart_session(base, key, session)
    else:  # logout | disconnect
        ok = await waha_service.logout_session(base, key, session)
        if action == "disconnect":
            await waha_service.delete_session(base, key, session)
            _release_whatsapp_provider(location_id)
    return {"success": ok}


def _release_whatsapp_provider(location_id: str) -> None:
    """
    Desconectar libera o provedor — e só o disconnect faz isso.

    `logout` e `restart` mantêm o tenant em WAHA de propósito: queda temporária
    de sessão não pode destravar a Z-API no meio de um reboot. Idempotente, para
    servir também de escape quando a sessão foi apagada por fora do painel.
    """
    db = SessionLocal()
    try:
        t = db.query(Tenant).filter(Tenant.location_id == location_id).first()
        if t:
            t.whatsapp_provider = "zapi"
            t.waha_session = None
            db.commit()
            logger.info(f"[CHANNEL] Provedor WhatsApp liberado em {location_id} (WAHA desconectado)")
            # A conta precisa refletir a troca: sem isto ela guarda a sessão WAHA
            # que não existe mais, e a referência apodrece.
            _agendar_sync(location_id, "whatsapp")
    except Exception as e:
        db.rollback()
        logger.error(f"[CHANNEL] Falha ao liberar provedor de {location_id}: {e}")
    finally:
        db.close()


def _agendar_sync(location_id: str, canal: str) -> None:
    """
    Sincroniza a conta de dentro de código SÍNCRONO.

    `_release_whatsapp_provider` é sync e roda dentro de um `finally`; não dá para
    await aqui. Chamar a versão síncrona direto é o caminho honesto — o custo é uma
    conexão, e este caminho é raro (troca de provedor), não quente.
    """
    from services.channel_accounts import _sincronizar_sync
    try:
        _sincronizar_sync(location_id, canal)
    except Exception as e:
        logger.error(f"[CONTA] sync pós-disconnect falhou em {location_id}: {type(e).__name__}")
