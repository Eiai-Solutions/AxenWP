"""
Motor LangChain + OpenRouter (comportamento atual, paridade por construção).

Envelopa exatamente a chamada que hoje mora em `AIEngine.generate_response`
(a linha `self.llm.ainvoke(messages_for_llm)`), sem mudar nada de comportamento.
É o motor default; o `ClaudeAgentEngine` (tool-use) entra depois atrás da flag
`AIAgent.agent_engine == "claude"`.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from services.agent_engine.base import AgentContext, AgentTurn


class LangChainAgentEngine:
    engine_name = "langchain"

    def __init__(self, llm):
        # Mesma instância de ChatOpenAI construída em AIEngine.__init__.
        self.llm = llm

    async def run(self, ctx: AgentContext) -> AgentTurn:
        messages: list[BaseMessage] = [SystemMessage(content=ctx.system_prompt)]
        for h in ctx.history:
            if h.get("role") == "assistant":
                messages.append(AIMessage(content=h["content"]))
            else:
                messages.append(HumanMessage(content=h["content"]))
        messages.append(HumanMessage(content=ctx.incoming_text))

        response = await self.llm.ainvoke(messages)
        # `raw` carrega a resposta LangChain para o pipeline extrair usage
        # (mantém o log de uso OpenRouter byte-idêntico ao atual).
        return AgentTurn(text=response.content, raw=response)
