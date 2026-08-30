"""Protocol boundary -- L5. Phase 10 · Step 11.

Persistent sidecar over NDJSON stdio (Decisions 2 and 3, Phase 00 §7): no port,
no auth, no TLS, and the parent process owns the lifetime. The ~1800 ms import
cost is paid once, behind the splash, instead of per command.
"""
