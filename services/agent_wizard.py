"""
As etapas de criação de um agente são DERIVADAS do que o tenant tem.

O problema que isto resolve: até aqui a config do agente tinha forma fixa, herdada
de quando o produto era só GoHighLevel. Hoje há tenant com CRM e tenant sem
(`mode='whatsapp_only'`), dois provedores de WhatsApp e Telegram — e a tela
perguntava as mesmas coisas para todos, incluindo funil de CRM para quem não tem CRM.

A regra: **cada etapa pergunta o que ela é capaz de aplicar.** Se não há CRM, a
etapa de qualificação não some — ela muda de forma, porque qualificar sem CRM
significa outra coisa (ver `qualification_handler`: sem funil, o `QualifiedLead`
é o destino, e aquela linha é o gate que pausa a IA para o humano).

Este módulo é função pura sobre o tenant: sem I/O, testável, e é ele que a tela
consulta para saber o que desenhar.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from services.channel_policy import active_whatsapp_provider, provider_label

# Identificadores estáveis — a tela e o estado do rascunho referenciam por eles.
CANAL = "canal"
IDENTIDADE = "identidade"
QUALIFICACAO = "qualificacao"
REVISAO = "revisao"


@dataclass(frozen=True)
class Etapa:
    id: str
    titulo: str
    descricao: str
    # A forma que a etapa assume neste tenant. A tela usa para escolher o corpo.
    variante: str
    # Contexto já resolvido (canais disponíveis, se há CRM…), para a tela não
    # precisar redescobrir nem chutar.
    dados: dict


def canais_disponiveis(tenant: Any) -> list[dict]:
    """Canais que este tenant PODE atender, com o motivo de cada um estar aí."""
    canais: list[dict] = []

    provedor = active_whatsapp_provider(tenant)
    if provedor:
        canais.append({
            "canal": "whatsapp",
            "rotulo": "WhatsApp",
            "detalhe": f"via {provider_label(provedor)}",
        })

    if (getattr(tenant, "telegram_bot_token", None) or "").strip():
        usuario = (getattr(tenant, "telegram_bot_username", None) or "").strip()
        canais.append({
            "canal": "telegram",
            "rotulo": "Telegram",
            "detalhe": f"@{usuario}" if usuario else "bot configurado",
        })

    return canais


def tem_crm(tenant: Any) -> bool:
    """
    CRM de verdade = modo `ghl` E credencial presente.

    `mode` sozinho não basta: um tenant pode estar marcado `ghl` e ainda não ter
    concluído o OAuth. Perguntar funil nesse estado seria oferecer uma lista vazia.
    """
    if str(getattr(tenant, "mode", "ghl") or "ghl") == "whatsapp_only":
        return False
    return bool(
        (getattr(tenant, "pit_token", None) or "").strip()
        or (getattr(tenant, "access_token", None) or "").strip()
    )


def etapas_para(tenant: Any) -> list[Etapa]:
    """As etapas que ESTE tenant precisa responder, na ordem."""
    canais = canais_disponiveis(tenant)
    crm = tem_crm(tenant)
    etapas: list[Etapa] = []

    # 1. Canal — só existe como pergunta se houver escolha a fazer.
    if len(canais) > 1:
        etapas.append(Etapa(
            id=CANAL,
            titulo="Onde o agente atende",
            descricao="Este agente responde por qual canal.",
            variante="escolha",
            dados={"canais": canais},
        ))
    elif len(canais) == 1:
        etapas.append(Etapa(
            id=CANAL,
            titulo="Onde o agente atende",
            descricao=f"{canais[0]['rotulo']} — o único canal conectado nesta instância.",
            variante="unico",
            dados={"canais": canais, "escolhido": canais[0]["canal"]},
        ))
    else:
        # Sem canal não há agente para criar. A etapa vira um bloqueio explícito
        # em vez de deixar a pessoa preencher tudo e falhar no fim.
        etapas.append(Etapa(
            id=CANAL,
            titulo="Nenhum canal conectado",
            descricao="Conecte o WhatsApp ou o Telegram antes de criar o agente.",
            variante="bloqueado",
            dados={"canais": []},
        ))
        return etapas

    # 2. Identidade — as três portas para o mesmo destino (ver
    #    decisoes/entrevista-da-mestre: todas produzem o mesmo form_data).
    etapas.append(Etapa(
        id=IDENTIDADE,
        titulo="Quem é o agente",
        descricao="A IA Mestre monta o agente a partir do que você contar sobre o negócio.",
        variante="portas",
        dados={"portas": [
            {"id": "entrevista", "rotulo": "Conversar com a Mestre",
             "detalhe": "Ela entrevista e monta o agente. Recomendado.", "recomendado": True},
            {"id": "formulario", "rotulo": "Preencher formulário",
             "detalhe": "14 campos sobre o negócio, se você já tem tudo à mão."},
            {"id": "manual", "rotulo": "Colar um prompt pronto",
             "detalhe": "Para quem já sabe exatamente o que quer."},
        ]},
    ))

    # 3. Qualificação — a etapa que MUDA DE FORMA conforme o tenant.
    if crm:
        etapas.append(Etapa(
            id=QUALIFICACAO,
            titulo="O que fazer com o lead qualificado",
            descricao="Com CRM conectado, o lead vira oportunidade no funil.",
            variante="crm",
            dados={"precisa_funil": True},
        ))
    else:
        etapas.append(Etapa(
            id=QUALIFICACAO,
            titulo="O que fazer com o lead qualificado",
            descricao=(
                "Sem CRM conectado: o lead é marcado e a IA pausa para atendimento "
                "humano. Nenhuma oportunidade é criada — não há funil."
            ),
            variante="sem_crm",
            dados={"precisa_funil": False},
        ))

    # 4. Revisão — o prompt gerado, editável, antes de existir agente de verdade.
    etapas.append(Etapa(
        id=REVISAO,
        titulo="Revisar e publicar",
        descricao="Leia o que a Mestre escreveu, ajuste se quiser, e publique.",
        variante="revisao",
        dados={},
    ))
    return etapas


def como_json(etapas: list[Etapa]) -> list[dict]:
    """Formato que a tela consome."""
    return [
        {"id": e.id, "titulo": e.titulo, "descricao": e.descricao,
         "variante": e.variante, "dados": e.dados}
        for e in etapas
    ]


def pode_publicar(rascunho: dict, etapas: list[Etapa]) -> tuple[bool, Optional[str]]:
    """
    Guard de publicação. O agente só passa a existir quando dá para atender.

    Mora aqui, e não na rota, pelo mesmo motivo das outras barreiras do projeto:
    a regra precisa valer por qualquer caminho.
    """
    # Lista vazia = tenant não resolvido. Falha FECHADA: sem as etapas não há guard
    # nenhum, e o de canal sumiria junto.
    if not etapas:
        return False, "Instância não encontrada."

    if any(e.variante == "bloqueado" for e in etapas):
        return False, "Nenhum canal conectado nesta instância."

    canal = rascunho.get("channel")
    if not canal:
        return False, "Escolha o canal do agente."

    # O canal precisa ser um dos que o tenant REALMENTE tem. Sem isto dava para
    # publicar em canal sem transporte — painel verde, nenhuma mensagem chegando —
    # ou apontar o publish para o slot do agente que atende hoje.
    etapa_canal = next((e for e in etapas if e.id == CANAL), None)
    permitidos = {c["canal"] for c in (etapa_canal.dados.get("canais") or [])} if etapa_canal else set()
    if permitidos and canal not in permitidos:
        return False, f"O canal '{canal}' não está conectado nesta instância."

    if not (rascunho.get("prompt") or "").strip():
        return False, "O agente ainda não tem prompt — conclua a etapa de identidade."
    return True, None
