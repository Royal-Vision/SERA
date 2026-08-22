"""Concrete tools. Each module owns exactly one tool and its input model.

Import discipline is inherited from base.py: stdlib + pydantic, plus anyio for
I/O. Nothing here may import langchain or langgraph -- the registry is built on
the fast path, before the first protocol frame.
"""

# NOTE ->> Do NOT build the registry here at import time. Export a build_registry(...)
# NOTE ->> that the entry point calls, so importing one tool for a test does not drag in
# NOTE ->> every other tool's dependencies.
