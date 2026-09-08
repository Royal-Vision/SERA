"""Run SERA through the signed-in Codex client."""

import asyncio

from app.agent.providers.codex_langchain import CodexChatModel
from app.agent.providers.openai_compat import CodexAuth
from langgraph.graph import MessagesState, StateGraph, END, START
from langchain.messages import HumanMessage

async def prompt_llm(state: MessagesState):
    async with await CodexAuth()() as codex:
        chat = CodexChatModel(codex=codex, model_name='gpt-5.6-luna')
        response = await chat.ainvoke(state['messages'])
        return {'messages': [response]}


async def main() -> None:
    async with await CodexAuth()() as codex:
        llm = CodexChatModel(codex=codex)
        print(await llm.ainvoke('hello'))

    output_response = await prompt_llm({'messages': [HumanMessage('hello')]})
    print(f'output_reponse: {output_response.response}')


if __name__ == '__main__':
    asyncio.run(main())

