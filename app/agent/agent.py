"""Run SERA through the signed-in Codex client."""

import asyncio

from app.agent.providers.codex_langchain import CodexChatModel
from app.agent.providers.openai_compat import CodexAuth

codex = asyncio.run(CodexAuth()())
llm = CodexChatModel(codex=codex)
output = asyncio.run(llm.ainvoke('hello'))
print(output)
