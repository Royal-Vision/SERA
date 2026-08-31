"""Agent orchestrator -- the deep agent SERA runs on."""

import asyncio

from deepagents import create_deep_agent

from app.agent.providers.openai_compat import CodexAuth


async def build_sera():
    """Check the credential, build the model, then the agent -- in that order.

    Async because the sign-in check drives the codex binary; the returned graph
    itself is ordinary. Not built at import time, so importing this module never
    spawns a subprocess or blocks on a login.
    """
    return create_deep_agent(model=await CodexAuth()())


if __name__ == "__main__":
    print(asyncio.run(build_sera()))
