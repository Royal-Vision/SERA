"""The agent loop, as a LangGraph StateGraph.

Why a hand-built graph rather than `create_agent`: tool execution must go through
`ToolEngine`, so every call gets the repair pipeline, permission gate, circuit breaker
and conflict-aware batching. `create_agent`'s built-in `ToolNode` would bypass all of it.
The loop itself is ~15 lines; the value is in what the tools node delegates to.

    START -> model -> (tool calls?) -> tools -> model -> ... -> END

LangGraph is imported INSIDE `build_agent()`. `import langgraph.graph` costs ~1800 ms on
this machine, and `sera --help` must not pay for it.
"""

from __future__ import annotations

import time
from typing import Annotated, Any, TypedDict

from app.agent.base import PermissionPolicy, ToolRegistry
from app.agent.contracts import AgentContext
from app.agent.engine import Outcome, ToolCall, ToolEngine

SYSTEM_PROMPT = """You are SERA, a coding agent working inside a user's project.

Tools:
- Use `glob` to find files by name, `grep` to search their contents. Prefer these over
  reading files one at a time.
- You MUST `read_file` a file before you `edit_file` it.
- `edit_file` replaces exact text. Copy `old_string` byte-for-byte from what you read,
  including indentation, and include enough surrounding context to be unique.
- Request independent tool calls together in one turn; they run in parallel.

Rules:
- Be concise. Do not narrate what you are about to do; do it, then state the result.
- If a tool returns an error, read it carefully -- it usually tells you exactly what to
  fix. Do not retry the identical call.
- Never invent file contents. Read first.
"""


def build_agent(
    registry: ToolRegistry,
    provider: str,
    model: str | None = None,
    *,
    max_steps: int = 12,
    system_prompt: str = SYSTEM_PROMPT,
) -> Any:
    """Compile the agent graph. Call once and cache -- compilation is not free."""
    from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
    from langgraph.graph import END, START, StateGraph
    from langgraph.graph.message import add_messages

    from app.agent.providers.base import get_model

    # Functional TypedDict syntax, deliberately. A `class _State(TypedDict)` declared
    # inside this function stores its annotations as *strings* (PEP 563/649), and
    # LangGraph resolves them with get_type_hints() against MODULE globals -- where
    # `add_messages` does not exist, because it is imported locally to keep langgraph
    # off the CLI's fast path. The functional form stores the real object instead.
    _State = TypedDict(
        "_State",
        {"messages": Annotated[list, add_messages], "steps": int},
    )

    llm = get_model(provider, model)
    schemas = [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.json_schema(),
            },
        }
        for t in registry
    ]
    llm_with_tools = llm.bind_tools(schemas)
    engine = ToolEngine(registry, PermissionPolicy())

    async def model_node(state: _State, config: Any) -> dict[str, Any]:
        msgs = state["messages"]
        if not any(isinstance(m, SystemMessage) for m in msgs):
            msgs = [SystemMessage(content=system_prompt), *msgs]
        response = await llm_with_tools.ainvoke(msgs, config)
        return {"messages": [response], "steps": state.get("steps", 0) + 1}

    async def tools_node(state: _State, config: Any) -> dict[str, Any]:
        last = state["messages"][-1]
        ctx: AgentContext = config["configurable"]["agent_context"]

        calls = [
            ToolCall(id=tc["id"], name=tc["name"], raw_args=tc.get("args", {}))
            for tc in getattr(last, "tool_calls", [])
        ]
        outcomes = await engine.execute_many(calls, ctx)

        if ctx.on_progress:
            for o in outcomes:
                mark = "ok" if o.outcome is Outcome.OK else o.outcome.value
                extra = f"  [repaired: {'; '.join(o.repairs)}]" if o.repairs else ""
                ctx.on_progress(f"  {o.tool_name} -> {mark} ({o.duration_ms:.0f}ms){extra}")

        return {
            "messages": [
                ToolMessage(
                    content=o.result.content,
                    tool_call_id=o.call_id,
                    status="error" if o.result.is_error else "success",
                )
                for o in outcomes
            ]
        }

    def should_continue(state: _State) -> str:
        last = state["messages"][-1]
        if state.get("steps", 0) >= max_steps:
            return END
        if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
            return "tools"
        return END

    graph = StateGraph(_State)
    graph.add_node("model", model_node)
    graph.add_node("tools", tools_node)
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "model")

    # No checkpointer: a one-shot CLI turn has nothing to resume, and a checkpointer
    # would add a write per superstep for no benefit.
    return graph.compile()


def make_context(cwd, provider: str, model: str, mode, on_progress=None) -> AgentContext:
    from app.agent.contracts import PermissionContext
    from app.agent.perf import new_id

    return AgentContext(
        cwd=cwd,
        permission=PermissionContext(mode=mode),
        session_id=new_id(),
        request_id=new_id(),
        provider=provider,
        model=model,
        deadline_at=time.monotonic() + 300.0,
        on_progress=on_progress,
    )
