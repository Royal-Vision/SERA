from deepagents import create_deep_agent
from app.agent.providers.openai_compat import codex

# NOTE -> inisilate the agent orchestrator
sera = create_deep_agent(
    model=codex
)

print(sera)