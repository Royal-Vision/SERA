# Learn LangGraph — practical starter folder

## Start here

Open the single consolidated notebook:
[LangGraph Production Guide](LangGraph_Production_Guide.ipynb).

It contains the learning path, runnable examples, production architecture,
operating guidance, local visuals, and official resources. The Markdown files
and standalone scripts remain as quick-reference copies.

This folder is a small, runnable learning path for **LangGraph**, the Python
framework used to orchestrate stateful agent and workflow graphs.

It is intentionally separate from [`app/agent/docs`](../app/agent/docs/README.md).
Those documents describe the target production architecture for SERA; this
folder teaches the LangGraph building blocks before you add them to the runtime.

## What LangGraph does

LangGraph runs a graph of steps. Each execution has:

```text
state ──> node ──> edge ──> next node ──> updated state
```

- **State** is the data shared by graph steps.
- **Nodes** are Python functions or agents that read state and return updates.
- **Edges** define the next step; they can be fixed or conditional.
- **Checkpoints** optionally persist progress, enabling memory, pauses, and
  recovery.

## Learning order

1. Read [Core concepts](01-core-concepts.md).
2. Follow [Install and run](02-install-and-run.md).
3. Run [Example 1: basic graph](examples/01_basic_graph.py).
4. Run [Example 2: conditional routing](examples/02_conditional_routing.py).
5. Read [Agent patterns](03-agent-patterns.md) before building multiple agents.

## Setup

LangGraph is not listed as a direct dependency in this repository yet. Add it
to the project when you are ready to run the examples:

```bash
pip install -U langgraph
```

If your normal project workflow uses `uv`, use this instead:

```bash
uv add langgraph
```

Then run an example from the repository root:

```bash
python learning-langgraph/examples/01_basic_graph.py
python learning-langgraph/examples/02_conditional_routing.py
```

These two examples need no API key or model: they teach graph mechanics first.

## When you are ready for an LLM agent

This repository already includes `langchain` and `langchain-google-genai`.
After adding `langgraph`, create an agent with an approved tool list and place
the graph integration in `app/agent/`, not in a FastAPI route. The production
design and safety requirements are documented in
[the agent architecture guide](../app/agent/docs/agent-architecture/README.md).

## Official references

- [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview)
- [Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)
- [Workflows and agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents)
- [Multi-agent patterns](https://langchain-ai.github.io/langgraph/tutorials/multi_agent/multi-agent-collaboration/)
