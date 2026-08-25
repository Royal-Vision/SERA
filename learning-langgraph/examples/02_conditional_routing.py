"""Run with: python learning-langgraph/examples/02_conditional_routing.py

This example teaches conditional edges. It has no LLM and no API key.
"""

from typing import Literal
from typing_extensions import TypedDict

from langgraph.graph import END, START, StateGraph


class RouteState(TypedDict):
    request: str
    route: str
    response: str


def classify(state: RouteState) -> dict[str, str]:
    """A deterministic node chooses a route from validated application logic."""
    route = "documentation" if "docs" in state["request"].lower() else "general"
    return {"route": route}


def choose_next(state: RouteState) -> Literal["documentation", "general"]:
    return state["route"]  # type: ignore[return-value]


def answer_docs(_: RouteState) -> dict[str, str]:
    return {"response": "Open the documentation learning path first."}


def answer_general(_: RouteState) -> dict[str, str]:
    return {"response": "Start with the basic graph example."}


builder = StateGraph(RouteState)
builder.add_node("classify", classify)
builder.add_node("documentation", answer_docs)
builder.add_node("general", answer_general)
builder.add_edge(START, "classify")
builder.add_conditional_edges("classify", choose_next)
builder.add_edge("documentation", END)
builder.add_edge("general", END)
app = builder.compile()


if __name__ == "__main__":
    final_state = app.invoke({"request": "Where are the docs?"})
    print(final_state["response"])
