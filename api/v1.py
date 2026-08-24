"""
API v1 — a superfície que um CRM de terceiro consome.

O primeiro pedaço do contrato: **o comando de ligar e desligar a IA**. É o que
faltava para "o controle é do CRM" sair do papel — até aqui só existia o botão do
painel, autenticado por cookie de humano, e o CRM não tem navegador.

Duas regras que valem para toda rota que entrar aqui:

1. **O `location_id` NUNCA vem do caminho.** Ele é derivado da chave, via
   `Depends(tenant_da_chave)`. Uma rota que aceitasse o tenant pela URL confiaria
   no cliente para dizer quem ele é — e bastaria um esquecimento numa rota nova
   para uma chave agir sobre outro tenant. Assim essa classe de bug não existe.

2. **`contact_ref` é opaco.** Telefone E.164 no WhatsApp, `chat_id` no Telegram.
   O hub não interpreta e o CRM não precisa saber a diferença; é a mesma string
   que chega no evento de entrada.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request

from services.tenant_auth import chave_do_rate_limit, tenant_da_chave
from utils.limiter import limiter
from utils.logger import logger

router = APIRouter(prefix="/api/v1", tags=["API v1"])

CANAIS = ("whatsapp", "telegram")


@router.get("/me")
@limiter.limit("60/minute", key_func=chave_do_rate_limit)
async def quem_sou_eu(request: Request, location_id: str = Depends(tenant_da_chave)):
    """
    Devolve de quem é a chave. Existe para o cliente conseguir testar a integração
    sem efeito colateral — sem isto, o primeiro teste de qualquer um seria pausar
    a IA de uma conversa real para ver se a chave funciona.
    """
    from data.database import SessionLocal
    from data.models import Tenant

    db = SessionLocal()
    try:
        t = db.query(Tenant).filter(Tenant.location_id == location_id).first()
        return {
            "location_id": location_id,
            "empresa": getattr(t, "company_name", None),
            "canais": [c for c in CANAIS],
        }
    finally:
        db.close()


@router.get("/conversations/{contact_ref}/ai")
@limiter.limit("120/minute", key_func=chave_do_rate_limit)
async def ler_estado_da_ia(
    request: Request,
    contact_ref: str,
    channel: str = "whatsapp",
    location_id: str = Depends(tenant_da_chave),
):
    """
    O estado atual da IA nesta conversa — para o painel do CRM reconciliar ao abrir.

    Sem esta leitura, a tela do CRM só sabe o que ela mesma mandou, e passa a
    mentir assim que a IA se pausa sozinha (qualificando ou escalando).
    """
    import asyncio

    from services import ai_gate

    if channel not in CANAIS:
        return {"success": False, "error": f"canal inválido: {channel}"}

    estado = await asyncio.to_thread(
        ai_gate._estado_sync, location_id, channel, contact_ref
    )
    if estado is None:
        # Ausência de linha é "nunca ninguém pausou", não "pausado".
        return {"enabled": True, "motivo": None, "until": None, "mudado_por": None}

    return {
        "enabled": estado["enabled"],
        "motivo": estado["motivo"],
        "until": estado["until"].isoformat() if estado["until"] else None,
        "mudado_por": estado["mudado_por"],
        # `vencida` explica por que `enabled` é True mesmo havendo pausa gravada.
        "pausa_vencida": estado["vencida"],
    }


@router.post("/conversations/{contact_ref}/ai")
@limiter.limit("60/minute", key_func=chave_do_rate_limit)
async def definir_estado_da_ia(
    request: Request,
    contact_ref: str,
    payload: dict = Body(...),
    location_id: str = Depends(tenant_da_chave),
):
    """
    Liga ou desliga a IA nesta conversa. **O comando que o CRM manda.**

    Corpo: `{"enabled": false, "channel": "whatsapp", "motivo": "...", "minutos": 120}`

    · `channel` pode ser omitido ao LIGAR (vale para todos os canais da conversa —
      quem religa está falando da pessoa, não do WhatsApp dela). Ao PAUSAR ele é
      obrigatório: adivinhar em quais canais criar a pausa seria chutar.
    · `minutos` dá prazo à pausa. Sem prazo, "assumi essa conversa" vira mudez
      eterna, e o lead que voltar semanas depois nunca mais é atendido.
    """
    import asyncio

    from services import ai_gate

    enabled = bool(payload.get("enabled"))
    channel = (payload.get("channel") or "").strip() or None
    motivo = (payload.get("motivo") or "").strip() or (None if enabled else ai_gate.CRM)
    minutos = payload.get("minutos")

    if channel and channel not in CANAIS:
        return {"success": False, "error": f"canal inválido: {channel}"}

    try:
        minutos = int(minutos) if minutos not in (None, "") else None
    except (TypeError, ValueError):
        return {"success": False, "error": "'minutos' precisa ser um número inteiro."}

    if enabled and not channel:
        n = await asyncio.to_thread(
            ai_gate.religar_todos_os_canais, location_id, contact_ref, "crm"
        )
        logger.info(f"[API] {location_id}: CRM religou a IA de {contact_ref} ({n} canal/is).")
        return {"success": True, "enabled": True, "canais": n}

    if not channel:
        return {"success": False,
                "error": "Informe 'channel' para pausar (whatsapp | telegram)."}

    ok = await asyncio.to_thread(
        ai_gate.definir,
        location_id=location_id, channel=channel, contact_ref=contact_ref,
        enabled=enabled, motivo=motivo, mudado_por="crm", minutos=minutos,
    )
    return {"success": ok, "enabled": enabled, "channel": channel}
