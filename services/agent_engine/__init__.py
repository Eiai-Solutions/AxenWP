"""
Fronteira de motor de agente (AgentEngine).

Isola o único ponto onde o AxenWP fala com o LLM, para que a troca de motor
(LangChain single-turn -> Claude Agent SDK / tool-use) seja um plugue atrás de
uma flag por-agente (`AIAgent.agent_engine`), sem reescrever a orquestração.

Ver docs/wiki/decisoes/reestruturacao-abstracoes-primeiro.md
"""

from services.agent_engine.base import AgentContext, AgentEngine, AgentTurn, ToolCall, ToolSpec
from services.agent_engine.claude_engine import ClaudeAgentEngine
from services.agent_engine.langchain_engine import LangChainAgentEngine

__all__ = [
    "AgentContext", "AgentEngine", "AgentTurn", "ToolCall", "ToolSpec",
    "LangChainAgentEngine", "ClaudeAgentEngine",
]
