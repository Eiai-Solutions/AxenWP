"""
Autenticidade dos webhooks de entrada — quem está batendo na porta é mesmo o provedor?

O buraco que isto fecha: até 2026-08-20, os três receivers de entrada validavam
apenas o FORMATO do `location_id`. Quem descobrisse um location_id — e o `/health`
público os entregava de graça até hoje — fazia POST forjado, e o hub tratava como
mensagem real: criava contato no CRM, rodava o agente (gastando o LLM do cliente),
mandava WhatsApp PELO NÚMERO DO CLIENTE para o telefone escolhido pelo atacante, e
gravava o texto dele em `chat_histories` — injeção de prompt direta no contexto do
agente, persistente.

O WAHA já tinha `_hmac_ok`, opt-in, com a chave vazia em produção. O
`zapi_webhook_secret` estava configurado e o código nunca o lia. O Telegram não
tinha nada.

## Três estados, não dois

Ligar verificação obrigatória de uma vez, num hub com atendimento no ar, é perder
mensagem: no instante em que o código passa a exigir, o webhook JÁ REGISTRADO no
provedor ainda não assina, e todo inbound vira 401 até alguém re-registrar.

Por isso o modo tem três posições (`WEBHOOK_AUTH_MODE`):

  · `off`      — não verifica. É o que vale sozinho quando não há segredo.
  · `observe`  — verifica, CONTA e LOGA, e **aceita mesmo assim**. É o default:
                 configurar o segredo nunca derruba nada. É aqui que o operador
                 descobre se o provedor está mesmo assinando.
  · `enforce`  — rejeita o que não assina.

O caminho seguro é: põe o segredo → re-registra o webhook → olha a métrica até
`ok` ser 100% → só então vira `enforce`. Nenhuma janela sem mensagem.

Sem segredo configurado, o canal fica em `off` faça o que fizer com o modo — não
adianta exigir assinatura de quem não tem com o que assinar.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Optional

from utils import metrics
from utils.config import settings
from utils.logger import logger

OFF = "off"
OBSERVE = "observe"
ENFORCE = "enforce"

# Header nativo do Telegram, definido por `setWebhook(secret_token=...)`.
HEADER_TELEGRAM = "x-telegram-bot-api-secret-token"
# Header do WAHA, HMAC-SHA512 hex do corpo cru.
HEADER_WAHA = "x-webhook-hmac"
# A Z-API não assina o corpo. O segredo vai num header que a gente escolhe quando
# o painel dela permite header customizado, e no PATH quando não permite.
HEADER_ZAPI = "x-chat-secret"


def modo() -> str:
    """`off` | `observe` | `enforce`. Valor inválido cai em `observe` (o seguro)."""
    m = (getattr(settings, "webhook_auth_mode", "") or OBSERVE).strip().lower()
    return m if m in (OFF, OBSERVE, ENFORCE) else OBSERVE


def _segredo(canal: str) -> str:
    campo = {
        "whatsapp_waha": "waha_webhook_hmac_key",
        "whatsapp_zapi": "zapi_webhook_secret",
        "telegram": "telegram_webhook_secret",
    }.get(canal, "")
    return (getattr(settings, campo, "") or "").strip() if campo else ""


def _iguais(enviado: str, esperado: str, *, hexa: bool = False) -> bool:
    """
    Comparação em tempo constante que não explode com lixo no header.

    `hmac.compare_digest` aceita `str` só se for ASCII puro — com qualquer byte
    acima de 0x7F ele levanta `TypeError`. E o header chega pelo Starlette já
    decodificado em latin-1, então "Ã" no header é um `str` não-ASCII perfeitamente
    possível: um scanner mandando lixo viraria 500 no lugar de uma rejeição limpa,
    justamente no painel que decide a virada para `enforce`. Comparar em BYTES não
    tem essa restrição.

    `hexa=True` só para o HMAC do WAHA: hex é case-insensitive e provedor que manda
    maiúscula não deve ser recusado. Para SEGREDO (Telegram, Z-API) a comparação é
    exata — normalizar caixa de um segredo joga entropia no lixo.
    """
    if hexa:
        enviado, esperado = enviado.lower(), esperado.lower()
    return hmac.compare_digest(enviado.encode("utf-8"), esperado.encode("utf-8"))


def _decidir(canal: str, valido: bool, motivo: str) -> bool:
    """
    Devolve se o request DEVE SER ACEITO, e registra o que aconteceu.

    A métrica é o instrumento do rollout: é olhando `resultado="falhou"` cair a
    zero que o operador sabe que pode virar a chave para `enforce`. Sem ela, virar
    a chave é apostar.
    """
    resultado = "ok" if valido else "falhou"
    metrics.inc("millochat_webhook_assinatura_total",
                labels={"canal": canal, "resultado": resultado, "modo": modo()})
    if valido:
        return True

    if modo() == ENFORCE:
        logger.warning(f"[WEBHOOK-AUTH] {canal}: REJEITADO — {motivo}")
        return False

    # Em `observe`, a mensagem passa. O log é WARNING e não INFO de propósito: ele
    # é o sinal de que o rollout ainda não terminou, e tem que incomodar.
    logger.warning(
        f"[WEBHOOK-AUTH] {canal}: assinatura inválida ({motivo}) — ACEITO porque o "
        f"modo é '{modo()}'. Enquanto isto aparecer, não vire para 'enforce'."
    )
    return True


def verificar_waha(raw: bytes, headers) -> bool:
    """HMAC-SHA512 do CORPO CRU. Reserializar o JSON mudaria os bytes e a ordem."""
    chave = _segredo("whatsapp_waha")
    if not chave or modo() == OFF:
        return True

    enviado = (headers.get(HEADER_WAHA) or "").strip()
    if not enviado:
        return _decidir("whatsapp_waha", False, "sem header de HMAC")

    esperado = hmac.new(chave.encode(), raw, hashlib.sha512).hexdigest()
    return _decidir("whatsapp_waha", _iguais(enviado, esperado, hexa=True),
                    "HMAC não confere")


def verificar_telegram(headers) -> bool:
    """
    Mecanismo NATIVO do Telegram: `setWebhook(secret_token=...)` faz ele mandar o
    valor em `X-Telegram-Bot-Api-Secret-Token` em todo update. Comparação em tempo
    constante mesmo sendo igualdade simples — é segredo.
    """
    segredo = _segredo("telegram")
    if not segredo or modo() == OFF:
        return True

    enviado = (headers.get(HEADER_TELEGRAM) or "").strip()
    if not enviado:
        return _decidir("telegram", False, "sem header de segredo")
    return _decidir("telegram", _iguais(enviado, segredo), "segredo não confere")


def verificar_zapi(headers, segredo_do_path: Optional[str] = None) -> bool:
    """
    A Z-API não assina o corpo. Aceita o segredo por header OU pelo caminho.

    O header é melhor: caminho aparece no log de acesso do proxy reverso, e um log
    é copiado, enviado a suporte e guardado por meses. O caminho existe porque o
    painel da Z-API pode não permitir header customizado — e um segredo no path é
    melhor que webhook nenhum.

    **Nunca aceitar por querystring.** Ela também vai para o log de acesso e ainda
    vaza em `Referer` — os dois defeitos do path, e mais um.
    """
    segredo = _segredo("whatsapp_zapi")
    if not segredo or modo() == OFF:
        return True

    enviado = (headers.get(HEADER_ZAPI) or "").strip() or (segredo_do_path or "").strip()
    if not enviado:
        return _decidir("whatsapp_zapi", False, "sem segredo no header nem no caminho")
    return _decidir("whatsapp_zapi", _iguais(enviado, segredo), "segredo não confere")


def estado_para_o_painel() -> dict:
    """
    O que o operador precisa ver para saber se pode virar a chave.

    Sem isto, "a assinatura está ligada?" só se responde lendo env var no servidor —
    e é justamente a pergunta que decide se dá para ir para `enforce`.
    """
    m = modo()
    return {
        "modo": m,
        "canais": {
            canal: {
                "segredo_configurado": bool(_segredo(canal)),
                # O que vale NA PRÁTICA para este canal: sem segredo, é `off`
                # mesmo que o modo global diga outra coisa.
                "efetivo": m if _segredo(canal) else OFF,
            }
            for canal in ("whatsapp_waha", "whatsapp_zapi", "telegram")
        },
    }
