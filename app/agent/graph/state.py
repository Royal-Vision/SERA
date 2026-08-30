"""Graph state -- Phase 08 §4.

Keep it small: every field is serialised on every checkpoint write.

  1. Never carry tool payloads in state. A 20 KB grep result belongs in the
     ToolMessage, not a state field re-serialised each superstep.
  2. `steps` is a hard ceiling, not telemetry -- it bounds worst-case turn
     latency when a model gets stuck in a tool loop.

NOTE ->> PYTHON 3.14 TRAP 1 (Phase 08 §4): declare the TypedDict with FUNCTIONAL
NOTE ->> syntax -- _State = TypedDict("_State", {...}). Class syntax stores
NOTE ->> annotations as strings (PEP 649) and LangGraph resolves them with
NOTE ->> get_type_hints() against MODULE globals, so a locally imported
NOTE ->> add_messages raises `NameError: name 'add_messages' is not defined`.
"""
