"""
Porta (contrato) do motor de agente.

O pipeline monta um `AgentContext` engine-agnóstico e recebe um `AgentTurn`.
Toda a lógica ao redor (transcrição de áudio, memória, IA Mestre / system prompt,
guardrails, qualificação, decisão de TTS) permanece no pipeline — só a chamada ao
LLM vive dentro de um `AgentEngine`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional, Protocol


# Handler de ferramenta: (nome, args, ctx) -> resultado. Efeitos colaterais ficam
# FORA do engine (no pipeline / handlers dedicados). Usado só pelo motor tool-use.
ToolDispatch = Callable[[str, dict, "AgentContext"], Awaitable[dict]]


@dataclass
class ToolSpec:
    """Descrição de uma ferramenta exposta ao modelo (formato JSON Schema)."""
    name: str
    description: str
    input_schema: dict


@dataclass
class AgentContext:
    """Tudo que o motor precisa para produzir um turno, já preparado pelo pipeline."""
    location_id: str
    session_id: str
    user_phone: str
    system_prompt: str
    # history: lista neutra [{"role": "user"|"assistant", "content": str}], 20 msgs.
    history: list
    incoming_text: str
    channel: str = "whatsapp"
    is_audio_input: bool = False
    agent_config: Any = None
    # Campos usados só pelo motor tool-use (Claude) — inertes no LangChainAgentEngine.
    tools: list = field(default_factory=list)          # list[ToolSpec]
    tool_dispatch: Optional[ToolDispatch] = None
    max_tool_iterations: int = 5
    enable_prompt_cache: bool = True


@dataclass
class ToolCall:
    name: str
    arguments: dict
    result: Optional[dict] = None


@dataclass
class AgentTurn:
    """Resultado de um turno do agente, engine-agnóstico."""
    text: str
    usage: dict = field(default_factory=dict)          # {"input_tokens", "output_tokens", ...}
    tool_calls: list = field(default_factory=list)     # list[ToolCall]
    events: dict = field(default_factory=dict)         # {"qualified_data", "escalate", ...}
    stop_reason: Optional[str] = None
    # Objeto de resposta específico do engine (ex.: resposta do LangChain).
    # Transitório: usado pelo pipeline para extrair usage no caminho LangChain.
    raw: Any = None


class AgentEngine(Protocol):
    """Contrato que todo motor de agente implementa."""
    engine_name: str

    async def run(self, ctx: AgentContext) -> AgentTurn:
        ...
