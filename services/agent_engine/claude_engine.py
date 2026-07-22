"""
Motor Claude (tool-use, Anthropic direto) — o loop `model → tool_use → tool_result`.

Substitui o single-turn do LangChain por um loop agentic: o modelo raciocina,
chama tools, recebe o resultado e itera até concluir. Os EFEITOS das tools são
executados pelo `ctx.tool_dispatch` que o pipeline injeta — o engine só orquestra
o loop e não conhece GHL/banco (mesma disciplina do LangChainAgentEngine).

Por que Anthropic direto (não OpenRouter): o prompt caching real (a maior alavanca
de custo, ~87%) depende do `cache_control` da API Anthropic. O cliente é INJETADO
no construtor (como o `llm` do LangChain) — assim este módulo não importa
`anthropic` no topo e os testes injetam um cliente falso.

Invariante de persistência: o par `tool_use`/`tool_result` vive só na lista LOCAL
do turno (memória). Nada disso vai para a memória de longo prazo (`chat_histories`),
que continua guardando apenas user + texto final — então não há risco de
`tool_use` órfão gerar 400 em cascata após um restart.
"""

from __future__ import annotations

from typing import Any, Optional

from services.agent_engine.base import AgentContext, AgentTurn, ToolCall
from utils.logger import logger

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_MAX_TOKENS = 1024


class ClaudeAgentEngine:
    engine_name = "claude"

    def __init__(self, client, model: Optional[str] = None, max_tokens: int = DEFAULT_MAX_TOKENS):
        # `client` é um anthropic.AsyncAnthropic (ou fake nos testes).
        self.client = client
        self.model = model or DEFAULT_MODEL
        self.max_tokens = max_tokens

    def _system_blocks(self, ctx: AgentContext) -> list:
        """
        System como lista de blocos, com o breakpoint de cache no fim do prefixo
        estável (tools → system). O histórico dinâmico vem depois, fora do cache.
        Sem `enable_prompt_cache`, manda texto puro (sem breakpoint).
        """
        block: dict = {"type": "text", "text": ctx.system_prompt}
        if ctx.enable_prompt_cache:
            block["cache_control"] = {"type": "ephemeral"}
        return [block]

    def _tool_params(self, ctx: AgentContext) -> list:
        return [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in (ctx.tools or [])
        ]

    def _initial_messages(self, ctx: AgentContext) -> list:
        msgs: list = []
        for h in ctx.history:
            role = "assistant" if h.get("role") == "assistant" else "user"
            msgs.append({"role": role, "content": h.get("content", "")})
        msgs.append({"role": "user", "content": ctx.incoming_text})
        return msgs

    @staticmethod
    def _final_text(content_blocks) -> str:
        partes = [b.text for b in content_blocks if getattr(b, "type", None) == "text"]
        return "\n".join(p for p in partes if p).strip()

    @staticmethod
    def _accumulate_usage(acc: dict, usage) -> None:
        acc["input_tokens"] = acc.get("input_tokens", 0) + getattr(usage, "input_tokens", 0)
        acc["output_tokens"] = acc.get("output_tokens", 0) + getattr(usage, "output_tokens", 0)
        acc["cache_read_input_tokens"] = acc.get("cache_read_input_tokens", 0) + getattr(usage, "cache_read_input_tokens", 0)
        acc["cache_creation_input_tokens"] = acc.get("cache_creation_input_tokens", 0) + getattr(usage, "cache_creation_input_tokens", 0)

    async def run(self, ctx: AgentContext) -> AgentTurn:
        messages = self._initial_messages(ctx)
        tools = self._tool_params(ctx)
        usage: dict = {}
        tool_calls: list[ToolCall] = []

        max_iters = max(1, int(ctx.max_tool_iterations or 5))
        for i in range(max_iters):
            resp = await self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=self._system_blocks(ctx),
                tools=tools,
                messages=messages,
            )
            self._accumulate_usage(usage, resp.usage)
            u = resp.usage
            logger.info(
                "[CLAUDE] turn=%s stop=%s | input=%s cache_write=%s cache_read=%s output=%s",
                i + 1, resp.stop_reason, getattr(u, "input_tokens", 0),
                getattr(u, "cache_creation_input_tokens", 0),
                getattr(u, "cache_read_input_tokens", 0), getattr(u, "output_tokens", 0),
            )

            if resp.stop_reason != "tool_use":
                return AgentTurn(
                    text=self._final_text(resp.content), usage=usage,
                    tool_calls=tool_calls, stop_reason=resp.stop_reason, raw=resp,
                )

            # Guarda o turno do assistant (com tool_use) na lista LOCAL, executa as
            # tools, e só então anexa os tool_results — encostados, como manda o
            # invariante (nada é persistido em memória de longo prazo aqui).
            messages.append({"role": "assistant", "content": [b.model_dump() for b in resp.content]})
            results: list = []
            for b in resp.content:
                if getattr(b, "type", None) != "tool_use":
                    continue
                out = await self._dispatch(ctx, b.name, dict(b.input or {}))
                tool_calls.append(ToolCall(name=b.name, arguments=dict(b.input or {}), result=out))
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": str(out)})
            messages.append({"role": "user", "content": results})

        # Estourou o teto de iterações: não deixa o usuário no vácuo (fallback).
        logger.warning("[CLAUDE] atingiu max_tool_iterations=%s para %s", max_iters, ctx.user_phone)
        return AgentTurn(
            text="", usage=usage, tool_calls=tool_calls, stop_reason="max_iterations",
        )

    async def _dispatch(self, ctx: AgentContext, name: str, args: dict) -> dict:
        """Executa a tool pelo dispatch do pipeline; erro vira tool_result (não quebra o loop)."""
        if ctx.tool_dispatch is None:
            return {"error": "tool_dispatch indisponível", "tool": name}
        try:
            out = await ctx.tool_dispatch(name, args, ctx)
            return out if isinstance(out, dict) else {"result": out}
        except Exception as e:  # noqa: BLE001 — devolve o erro ao modelo, não derruba o turno
            logger.error("[CLAUDE] erro na tool %s: %s", name, e)
            return {"error": str(e), "tool": name}
