# 1. Core concepts

## The smallest useful graph

A graph is compiled from a shared state, nodes, and edges:

```python
from typing_extensions import TypedDict
from langgraph.graph import END, START, StateGraph


class State(TypedDict):
    subject: str
    greeting: str


def greet(state: State) -> dict:
    return {"greeting": f"Hello, {state['subject']}!"}


graph = StateGraph(State)
graph.add_node("greet", greet)
graph.add_edge(START, "greet")
graph.add_edge("greet", END)
app = graph.compile()

print(app.invoke({"subject": "SERA"}))
```

`START` and `END` are special graph markers. The `greet` node receives the
current state and returns only the fields it changes. LangGraph merges that
update into state before following the next edge.

## State design

State is the contract between your nodes. Keep it small and explicit.

```text
Good:  request, messages, tool_results, approval_status, stop_reason
Avoid: a single untyped dict that every node changes arbitrarily
```

Use a `TypedDict` for simple learning examples. In this project, the runtime
should use the typed contracts in `app/agent/contracts.py` at system boundaries.

## Nodes

A node can be a deterministic function, a model call, a tool executor, a
validator, or a complete subgraph. It should have one clear responsibility.

```text
validate request → call model → authorize tool → execute tool → summarize
```

Nodes return updates; avoid mutating the incoming `state` object in place.

## Edges and routing

- A normal edge always goes to the same next node.
- A conditional edge calls a routing function and chooses a next node based on
  state.
- A graph can branch into independent work and later join results.

The routing example in this folder shows a conditional edge without any LLM.

## Agent versus workflow

A **workflow** has code-chosen paths: validation, approval, retrieval, and
other steps run in a defined order. An **agent** uses a model to choose actions
or tools dynamically. Production systems usually combine both:

```text
deterministic policy check → agent/tool loop → deterministic audit record
```

Use deterministic logic for security, authorization, schemas, budgets, and
critical business rules. Do not ask a model to enforce those policies.

## Operating model

At runtime you normally use one of these calls:

```python
result = app.invoke(input_state)   # run to completion
for event in app.stream(input_state):
    print(event)                   # show progress as nodes run
```

Later, a checkpointer plus a `thread_id` gives a graph durable state. That is
the foundation for multi-turn conversations, human approvals, and recovery.
