"""Run SERA through the signed-in Codex client."""

import asyncio

from app.agent.providers.codex_langchain import CodexChatModel
from app.agent.providers.openai_compat import CodexAuth
from langgraph.graph import MessagesState, StateGraph, END, START


codex = asyncio.run(CodexAuth()())
llm = CodexChatModel(codex=codex)
output = asyncio.run(llm.ainvoke('hello'))
print(output)


async def prompt_llm(state: MessagesState):
    async with CodexAuth()() as codex:
        codex:CodexChatModel = await CodexChatModel(codex=codex, model_name='GPT_5.5 Luna')
        response = await codex.ainvoke(state['messages'])
