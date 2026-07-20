"""
Política de canal: uma instância tem no máximo UM provedor de WhatsApp ativo.

O provedor ativo é DERIVADO do estado real do tenant, nunca do flag sozinho. A
coluna `whatsapp_provider` carrega a *intenção* — a migration 022 carimbou
"zapi" em todo tenant existente, inclusive nos que nunca tiveram credencial —
enquanto as credenciais dizem o que de fato existe. Derivar evita os dois
enganos simétricos:

  · tenant marcado "zapi" mas sem credencial nenhuma NÃO pode bloquear o WAHA;
  · tenant marcado "waha" mas sem sessão NÃO pode bloquear a Z-API.

Funções puras, só leem atributos do objeto Tenant já carregado — são chamadas
no caminho quente do webhook e não podem fazer I/O.
"""

from typing import Optional

WAHA = "waha"
ZAPI = "zapi"

_LABELS = {WAHA: "WAHA", ZAPI: "Z-API"}
_ARTICLES = {WAHA: "o", ZAPI: "a"}


def provider_label(provider: Optional[str]) -> str:
    """Nome do provedor como o operador o vê no painel."""
    return _LABELS.get(provider or "", provider or "")


def provider_with_article(provider: Optional[str]) -> str:
    """"a Z-API" / "o WAHA" — para a frase não sair torta no painel."""
    label = provider_label(provider)
    if not label:
        return ""
    return f"{_ARTICLES.get(provider or '', 'o')} {label}"


def _filled(tenant, attr: str) -> bool:
    return bool((getattr(tenant, attr, None) or "").strip())


def active_whatsapp_provider(tenant) -> Optional[str]:
    """
    Provedor de WhatsApp efetivamente ativo na instância, ou None se nenhum.

    WAHA vence quando há intenção + sessão gravada; a Z-API entra quando há
    credencial completa (instance_id + token). Credencial da Z-API que sobrou
    de uma configuração antiga fica dormente sob o WAHA — não é apagada, mas
    também não conta como ativa.
    """
    if tenant is None:
        return None
    flag = (getattr(tenant, "whatsapp_provider", None) or "").strip().lower()
    if flag == WAHA and _filled(tenant, "waha_session"):
        return WAHA
    if _filled(tenant, "zapi_instance_id") and _filled(tenant, "zapi_token"):
        return ZAPI
    return None


def whatsapp_conflict(tenant, target: str) -> Optional[str]:
    """
    Provedor que bloqueia a configuração de `target`, ou None se o caminho está livre.

    Configurar o provedor que já está ativo é sempre permitido (é edição, não troca).
    """
    active = active_whatsapp_provider(tenant)
    if active is None or active == target:
        return None
    return active


def conflict_message(blocking: str, target: str) -> str:
    """Mensagem única para operador — usada no redirect do form e no JSON do connect."""
    return (
        f"WhatsApp já conectado via {provider_label(blocking)} nesta instância. "
        f"Desconecte {provider_with_article(blocking)} antes de configurar "
        f"{provider_with_article(target)} — é um provedor de WhatsApp por instância."
    )
