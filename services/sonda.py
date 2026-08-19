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


# Critérios reconhecidos, na ordem em que são reportados.
_CRITERIOS = ("espera_tool", "nao_espera_tool", "espera_texto", "nao_espera_texto")


def avaliar(caso: dict, chamadas: list[str], texto: str) -> tuple[bool, str, list[str]]:
    """
    Avalia TODOS os critérios do caso. Todos precisam valer.

    Era um `if/elif`: o caso afirmava só o PRIMEIRO critério que tivesse, e os
    demais eram lidos do JSON e silenciosamente ignorados. Custou caro em
    2026-08-19 — um caso que checava "saudou?" e "respondeu o lead?" só checava a
    saudação, então o agente passou a devolver "em que posso te ajudar?" para quem
    já havia perguntado, e a medição marcou `ok`. Pior: o ciclo de treino leu esse
    `ok` como conserto e recomendou publicar a piora.

    Devolve (ok, descrição de todos os critérios, lista dos que reprovaram).
    """
    checagens: list[tuple[bool, str]] = []
    if caso.get("espera_tool"):
        alvo = caso["espera_tool"]
        checagens.append((alvo in chamadas, f"deve chamar {alvo}"))
    if caso.get("nao_espera_tool"):
        alvo = caso["nao_espera_tool"]
        checagens.append((alvo not in chamadas, f"NÃO pode chamar {alvo}"))
    if caso.get("espera_texto"):
        rx = caso["espera_texto"]
        checagens.append((bool(re.search(rx, texto, re.I)), f"texto casa /{rx}/"))
    if caso.get("nao_espera_texto"):
        rx = caso["nao_espera_texto"]
        checagens.append((not re.search(rx, texto, re.I), f"texto NÃO casa /{rx}/"))

    if not checagens:
        return True, "(sem critério)", []

    falhou_em = [desc for ok, desc in checagens if not ok]
    return (not falhou_em), " E ".join(desc for _, desc in checagens), falhou_em


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

    # A sonda é gasto da MESTRE, não do atendimento: nenhum lead foi atendido
    # aqui, o que se está pagando é medição de agente. Contar como atendimento
    # inflaria o custo por conversa e esconderia o preço de treinar.
    from services.usage_logger import registrar_gasto_mestre
    await registrar_gasto_mestre(
        agente.location_id,
        (getattr(agente, "anthropic_model", None) or "").strip() or "claude-sonnet-5",
        turn.usage,
    )

    chamadas = [c.name for c in (turn.tool_calls or [])]
    texto_bruto = turn.text or ""
    ok, criterio, falhou_em = avaliar(caso, chamadas, texto_bruto)

    return {
        "id": caso["id"],
        "ok": ok,
        "criterio": criterio,
        # Qual critério reprovou — com vários por caso, "FALHA" sozinho não diz nada.
        "falhou_em": falhou_em,
        "chamou": ",".join(chamadas) or "-",
        "texto": texto_bruto.replace("\n", " ")[:90],
        # O truncado em 90 serve à linha da CLI. Julgar a resposta precisa dela
        # inteira: foi lendo o texto completo que se viu o agente devolver
        # "em que posso te ajudar?" para quem já tinha perguntado.
        "texto_completo": texto_bruto,
    }


