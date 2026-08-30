"""Tool execution engine -- L3. Phase 05 · the differentiator.

This package is the reason SERA occupies position C rather than B
(Phase 00 §4): `create_agent` bundles `ToolNode`, and `ToolNode` executes tool
calls itself -- which is precisely the code in here. Six capabilities do not
survive that trade: argument repair, fuzzy tool-name resolution, conflict-aware
batching, the circuit breaker, errors rendered as prompts, per-tool budget_ms.

Import rule (Phase 00 §6): engine/ never imports graph/. The engine must run
under the Step 4 stdio loop with no langgraph on the import path at all.
"""
