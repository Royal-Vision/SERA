"""Turn driver + session persistence -- Phase 10.

Owns the lifetime of AgentContext (contracts.py): one instance per TURN, from
which a ToolRuntimeContext is minted per CALL. Also where on_progress and
on_permission_request are actually wired to the terminal.

NOTE ->> on_permission_request=None means nobody can answer, so ASK becomes
NOTE ->> DENY (contracts.py). That default is what keeps the core free of any
NOTE ->> terminal dependency -- it is a design decision, not a missing wire.
"""
