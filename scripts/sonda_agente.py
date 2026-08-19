#!/usr/bin/env python
"""
Sonda: mede o COMPORTAMENTO de um agente SDK contra roteiros de lead.

Existe porque, até 2026-08-18, não havia como saber se uma mudança de prompt
melhorou ou piorou o agente. O botão "Aplicar melhoria" reescrevia o prompt de
produção e o operador clicava e torcia — com 6.6k caracteres em jogo.

O que ela faz: carrega um `AIAgent` REAL do banco, monta o contexto pelo MESMO
caminho de produção (`build_system_prompt` + `build_tool_specs`), roda o
`ClaudeAgentEngine` de verdade contra falas de lead roteirizadas, e reporta QUAL
FERRAMENTA o agente chamou em cada caso.

Mede AÇÃO, não texto. Texto é estilo e muda a cada rodada; a ação é o produto —
escalar quando não devia é o defeito que fecha a venda no meio.

    python scripts/sonda_agente.py --location jVxHh2Elz8MxMurLzwzz
    python scripts/sonda_agente.py --location X --canal telegram --csv antes.csv

Custa dinheiro: uma chamada Anthropic por caso (o prefixo é cacheado, então do 2º
em diante sai mais barato). Use antes e depois de mexer no prompt, e compare.

O par de custo zero é `tests/test_agente_casos.py`, que roda os MESMOS roteiros
contra um cliente falso — lá se mede o encanamento (a política chega ao modelo?),
aqui se mede o julgamento.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

ROTEIRO_PADRAO = pathlib.Path(__file__).resolve().parent.parent / "tests" / "roteiros" / "comportamento_sdr.json"


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

    if espera:
        ok = espera in chamadas
        criterio = f"deve chamar {espera}"
    elif nao_espera:
        ok = nao_espera not in chamadas
        criterio = f"NÃO pode chamar {nao_espera}"
    else:
        ok, criterio = True, "(sem critério)"

    return {
        "id": caso["id"],
        "ok": ok,
        "criterio": criterio,
        "chamou": ",".join(chamadas) or "-",
        "texto": (turn.text or "").replace("\n", " ")[:90],
    }


async def main() -> int:
    ap = argparse.ArgumentParser(description="Mede o comportamento de um agente SDK.")
    ap.add_argument("--location", required=True)
    ap.add_argument("--canal", default="whatsapp")
    ap.add_argument("--roteiro", type=pathlib.Path, default=ROTEIRO_PADRAO)
    ap.add_argument("--csv", type=pathlib.Path, help="grava o resultado para comparar depois")
    ap.add_argument("--historico", type=int, metavar="ID",
                    help="mede uma VERSÃO ANTIGA do prompt (id em agent_prompt_history). "
                         "É assim que se prova que uma mudança melhorou: rode o antes e o depois.")
    args = ap.parse_args()

    from data.database import SessionLocal
    from data.models import AIAgent

    db = SessionLocal()
    agente = (
        db.query(AIAgent)
        .filter(AIAgent.location_id == args.location, AIAgent.channel == args.canal)
        .first()
    )
    db.close()
    if agente is None:
        print(f"agente não encontrado: {args.location}/{args.canal}")
        return 2

    prompt_override = None
    if args.historico:
        from data.models import AgentPromptHistory
        db = SessionLocal()
        v = db.query(AgentPromptHistory).filter(AgentPromptHistory.id == args.historico).first()
        db.close()
        if v is None:
            print(f"versão {args.historico} não encontrada")
            return 2
        prompt_override = v.prompt or ""
        print(f"MEDINDO A VERSÃO #{args.historico} do prompt ({len(prompt_override)} chars)")

    from services.agent_engine.tools import build_tool_specs

    tools = [t.name for t in build_tool_specs(agente)]
    print(f"agente : {agente.name} ({args.location}/{args.canal})")
    print(f"prompt : {len(prompt_override if prompt_override is not None else (agente.prompt or ''))} chars")
    print(f"tools  : {tools or 'NENHUMA'}")
    if "register_qualified_lead" not in tools:
        # Não é detalhe: sem campos de coleta a única ação que o agente conhece é
        # transferir, e todo caso de "não escala" fica muito mais difícil.
        print("         (sem campos de coleta: o agente só sabe ESCALAR)")
    print()

    linhas = []
    for caso in carregar_roteiros(args.roteiro):
        try:
            r = await rodar_caso(agente, caso, prompt_override)
        except Exception as e:
            r = {"id": caso["id"], "ok": False, "criterio": "erro",
                 "chamou": f"{type(e).__name__}", "texto": str(e)[:90]}
        linhas.append(r)
        marca = "ok  " if r["ok"] else "FALHA"
        print(f"  {marca} {r['id']:26} chamou={r['chamou']:22} {r['criterio']}")
        if r["texto"]:
            print(f"        └ {r['texto']}")

    acertos = sum(1 for l in linhas if l["ok"])
    print(f"\n{acertos}/{len(linhas)} casos no comportamento esperado")

    if args.csv:
        import csv
        with args.csv.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["id", "ok", "criterio", "chamou", "texto"])
            w.writeheader()
            w.writerows(linhas)
        print(f"gravado em {args.csv} — rode de novo depois de mudar o prompt e compare")

    return 0 if acertos == len(linhas) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
