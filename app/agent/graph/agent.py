"""build_agent() -- Phases 08-09 · Step 10.

StateGraph wiring only: model node, tool node delegating to engine/executor.py,
and the conditional edge between them. No tool logic lands here; if it does, the
position-C argument (Phase 00 §4) has quietly been given up.

NOTE ->> PYTHON 3.14 TRAP 2 (Phase 08 §4): add_conditional_edges calls
NOTE ->> get_type_hints() on the branch function. Annotating it `state: _State`
NOTE ->> with a function-local _State raises `NameError: name '_State' is not
NOTE ->> defined`. Leave the parameter unannotated, or annotate it `dict`.

NOTE ->> perf.py is recoverable from git (the deletion of app/agent/_old/ is not
NOTE ->> committed): `git checkout -- app/agent/_old/perf.py`, then git mv it up.
NOTE ->> BUILD-ORDER Step 10 wants it back before the entry point exists.
"""
