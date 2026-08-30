"""Read-before-edit state machine -- Phase 06 · Step 7.

The failure this prevents is not an error: it is a SUCCESSFUL edit applied to a
file the agent last saw three turns ago, silently discarding what changed in
between. An error is recoverable; silent data loss is not (see edit.py).

The per-turn file-state tracker lives in AgentContext.extras (contracts.py) --
per turn, not per process, because staleness is a property of the conversation.

NOTE ->> tools/_fs.py already carries require_fresh_read/file_state/fingerprint.
NOTE ->> Decide deliberately whether this module OWNS that state machine and _fs
NOTE ->> defers to it, or whether this module is only the engine-side gate. Two
NOTE ->> copies of a freshness rule is how the two disagree.
"""
