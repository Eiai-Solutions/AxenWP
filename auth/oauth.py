"""
Fluxo OAuth 2.0 do GoHighLevel.
Endpoints para instalação de novos tenants e callback de autorização.
"""

from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request
from fastapi.responses import RedirectResponse, JSONResponse

from utils.config import settings
from utils.logger import logger
from auth.token_manager import token_manager

import httpx

# Variável de memória para guardar o CI/CS customizado do UI
_temp_oauth_secrets = {}


router = APIRouter(prefix="/oauth", tags=["OAuth"])

# =============================================================================
# URL base de autorização do GHL
# =============================================================================
GHL_AUTH_URL = "https://marketplace.gohighlevel.com/oauth/chooselocation"


@router.get("/install")
async def oauth_install(
    request: Request,
    company: str = Query(..., description="Nome da empresa"),
    ci: str = Query(None, description="Custom Client ID from UI"),
    cs: str = Query(None, description="Custom Client Secret from UI"),
    ui_redirect: str = Query(None),
    existing: str = Query(None, description="Location ID de tenant existente para vincular CRM"),
):
    """
    Inicia o fluxo de instalação OAuth para uma nova empresa.
    Se 'existing' for passado, vincula o CRM a um tenant existente (whatsapp_only).
    Redireciona o usuário para a tela de autorização do GHL.
    """
    client_id = ci or settings.ghl_client_id

    # Se veio do UI, guarda em memória temporária os dados para o callback resgatar pela sessão/origem
    state_key = company
    _temp_oauth_secrets[state_key] = {"ci": client_id, "cs": cs or settings.ghl_client_secret, "redirect": ui_redirect, "existing": existing}

    params = {
        "response_type": "code",
        "redirect_uri": settings.ghl_redirect_uri,
        "client_id": client_id,
        "state": state_key,
        "scope": (
            "conversations.readonly "
            "conversations.write "
            "conversations/message.readonly "
            "conversations/message.write "
            "contacts.readonly "
            "contacts.write "
            "locations.readonly "
            "locations/customFields.readonly "
            "opportunities.readonly "
            "opportunities.write"
        ),
    }

    auth_url = f"{GHL_AUTH_URL}?{urlencode(params)}"
    logger.info(f"Redirecionando para OAuth GHL: empresa={company}")
    return RedirectResponse(url=auth_url)


@router.get("/callback")
async def oauth_callback(
    code: str = Query(None, description="Authorization code do GHL"),
    error: str = Query(None, description="Erro retornado pelo GHL"),
    state: str = Query(None, description="State (Company Name)")
):
    """
    Callback do OAuth. Recebe o authorization_code e troca por tokens.
    """
    # Resgata segredos do dictionary temporário para o caso multi-app (Vindo da UI)
    temp_data = _temp_oauth_secrets.pop(state, {}) if state else {}
    client_id = temp_data.get("ci", settings.ghl_client_id)
    client_secret = temp_data.get("cs", settings.ghl_client_secret)
    ui_redirect = temp_data.get("redirect")
    existing_location_id = temp_data.get("existing")

    if error:
        logger.error(f"Erro no OAuth callback: {error}")
        if ui_redirect:
            return RedirectResponse(url=f"/admin/dashboard?err=Autorização negada pelo GHL", status_code=303)
        return JSONResponse(
            status_code=400,
            content={"error": error, "message": "Autorização negada pelo GHL"},
        )

    if not code:
        if ui_redirect:
            return RedirectResponse(url=f"/admin/dashboard?err=Code não recebido", status_code=303)
        return JSONResponse(
            status_code=400,
            content={"error": "missing_code", "message": "Authorization code não recebido"},
        )

    # Trocar o código por tokens
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{settings.ghl_api_base}/oauth/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "grant_type": "authorization_code",
                    "code": code,
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
                    f"Falha ao trocar code por token: "
                    f"status={response.status_code}, body={response.text}"
                )
                return JSONResponse(
                    status_code=500,
                    content={
                        "error": "token_exchange_failed",
                        "message": "Falha ao obter tokens do GHL",
                        "details": response.text,
                    },
                )

            data = response.json()

    except Exception as e:
        logger.error(f"Exceção ao trocar code por token: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": "connection_error", "message": str(e)},
        )

    # Extrair dados da resposta
    ghl_location_id = data.get("locationId", "")
    company_id = data.get("companyId", "")

    if not ghl_location_id:
        logger.warning("locationId não retornado no token. Verificando companyId...")

    # Se estamos vinculando CRM a um tenant existente (whatsapp_only)
    if existing_location_id:
        tenant = token_manager.link_ghl_to_existing_tenant(
            existing_location_id=existing_location_id,
            ghl_location_id=ghl_location_id or company_id,
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_in=data.get("expires_in", 86399),
            client_id=client_id,
            client_secret=client_secret,
        )
        if not tenant:
            if ui_redirect:
                return RedirectResponse(url=f"/admin/dashboard?err=Tenant {existing_location_id} não encontrado.", status_code=303)
            return JSONResponse(status_code=404, content={"error": "Tenant não encontrado"})

        logger.info(f"✅ CRM vinculado ao tenant existente: {tenant.company_name} (ghl_location_id={ghl_location_id})")

        if ui_redirect:
            return RedirectResponse(url=f"/admin/dashboard?msg=CRM conectado para {tenant.company_name} com sucesso!", status_code=303)

        return JSONResponse(status_code=200, content={"success": True, "message": "CRM vinculado com sucesso!"})

    # Registrar novo tenant (fluxo padrão)
    tenant = token_manager.register_tenant(
        location_id=ghl_location_id or company_id,
        company_name=state or f"Empresa-{ghl_location_id or company_id}",
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        expires_in=data.get("expires_in", 86399),
        client_id=client_id,
        client_secret=client_secret
    )

    logger.info(
        f"✅ Novo tenant registrado: {tenant.company_name} "
        f"(location_id={tenant.location_id})"
    )

    if ui_redirect:
        return RedirectResponse(url=f"/admin/dashboard?msg=Instalação para {tenant.company_name} concluída com sucesso! Agora defina o Z-API.", status_code=303)

    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "message": f"Empresa registrada com sucesso!",
            "location_id": tenant.location_id,
            "instructions": (
                "Empresa conectada ao banco de dados! "
                "Agora configure o Z-API pelo painel administrativo."
            ),
        },
    )
