"""
O núcleo da sonda: rodar UM caso contra um agente e dizer o que ele FEZ.

Mora em `services/` e não em `scripts/` porque tem dois consumidores em produção:
a CLI (`scripts/sonda_agente.py`) e o ciclo de treino (`services/mestre_ciclo.py`),
que roda dentro da API. `scripts/` não vai na imagem Docker — um serviço importando
de lá quebra só em produção, que foi exatamente o que aconteceu.

O que ela mede: qual FERRAMENTA o agente chamou, e o que ele DISSE. Ação e texto,
não impressão.
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Optional

# O roteiro de regressão é DADO DE PRODUÇÃO agora (o ciclo de treino roda contra
# ele), então mora junto do serviço e vai na imagem.
ROTEIRO_PADRAO = pathlib.Path(__file__).resolve().parent / "roteiros" / "comportamento_sdr.json"


def carregar_roteiros(caminho: pathlib.Path) -> list[dict]:
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    return dados["casos"]


async def rodar_caso(agente, caso: dict, prompt_override: str | None = None) -> dict:
    """Um caso. Devolve o que o agente FEZ, não o que ele disse."""
    from services.agent_engine.base import AgentContext
    from services.agent_engine.claude_engine import ClaudeAgentEngine
    from services.agent_engine.tools import build_tool_specs
    from services.prompt_builder import build_system_prompt

    tools = build_tool_specs(agente)
    system = build_system_prompt(
        prompt_override if prompt_override is not None else (agente.prompt or ""),
        qualification_enabled=bool(agente.qualification_enabled),
        qualification_fields=agente.qualification_fields or [],
        for_tools=True,
    )

    async def dispatch_seco(name, args, ctx):
        # Seco de propósito: a sonda mede a DECISÃO do agente, não o efeito
        # colateral. Nada é gravado no CRM nem pausa conversa de ninguém.
        return {"status": "ok", "message": "(sonda: efeito não executado)"}

    ctx = AgentContext(
        location_id=agente.location_id,
        session_id=f"sonda_{caso['id']}",
        user_phone="5500000000000",
        system_prompt=system,
        history=list(caso.get("historico") or []),
        incoming_text=caso["lead"],
        channel=agente.channel or "whatsapp",
        is_audio_input=bool(caso.get("audio")),
        agent_config=agente,
        tools=tools,
        tool_dispatch=dispatch_seco,
    )

    # A chave sai da MESMA cascata do runtime (agente → admin → env). Resolver por
    # conta própria daria uma sonda medindo um agente que não existe em produção.
    from anthropic import AsyncAnthropic
    from services.ai_service import AIEngine

    chave = AIEngine._resolve_anthropic_key(None, agente)
    if not chave:
        raise RuntimeError("sem chave Anthropic (nem no agente, nem em Config. Admin, nem no ambiente)")
    engine = ClaudeAgentEngine(
        AsyncAnthropic(api_key=chave),
        model=(getattr(agente, "anthropic_model", None) or "").strip() or None,
    )
    turn = await engine.run(ctx)

    chamadas = [c.name for c in (turn.tool_calls or [])]
    espera = caso.get("espera_tool")
    nao_espera = caso.get("nao_espera_tool")
    # Critério de TEXTO, para pedidos do operador que não são sobre ferramenta.
    # "O agente tem que começar com bom dia/boa tarde/boa noite" é verificável por
    # regex — e é assim que o pedido dele vira medida, em vez de opinião.
    espera_texto = caso.get("espera_texto")
    nao_espera_texto = caso.get("nao_espera_texto")
    texto_bruto = turn.text or ""

    if espera:
        ok = espera in chamadas
        criterio = f"deve chamar {espera}"
    elif nao_espera:
        ok = nao_espera not in chamadas
        criterio = f"NÃO pode chamar {nao_espera}"
    elif espera_texto:
        ok = bool(re.search(espera_texto, texto_bruto, re.I))
        criterio = f"texto casa /{espera_texto}/"
    elif nao_espera_texto:
        ok = not re.search(nao_espera_texto, texto_bruto, re.I)
        criterio = f"texto NÃO casa /{nao_espera_texto}/"
    else:
        ok, criterio = True, "(sem critério)"

    return {
        "id": caso["id"],
        "ok": ok,
        "criterio": criterio,
        "chamou": ",".join(chamadas) or "-",
        "texto": (turn.text or "").replace("\n", " ")[:90],
    }


