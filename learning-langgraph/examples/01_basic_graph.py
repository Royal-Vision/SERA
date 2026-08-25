"""Run with: python learning-langgraph/examples/01_basic_graph.py

This example has no LLM and no API key. It teaches StateGraph mechanics.
"""

from typing_extensions import TypedDict

from langgraph.graph import END, START, StateGraph


class LessonState(TypedDict):
    topic: str
    lesson: str


def make_lesson(state: LessonState) -> dict[str, str]:
    """A node receives the current state and returns only its update."""
    return {"lesson": f"LangGraph represents {state['topic']} as a graph."}


builder = StateGraph(LessonState)
builder.add_node("make_lesson", make_lesson)
builder.add_edge(START, "make_lesson")
builder.add_edge("make_lesson", END)
app = builder.compile()


if __name__ == "__main__":
    final_state = app.invoke({"topic": "agent workflows"})
    print(final_state["lesson"])
