"""Ollama provider -- Phase 07.

NOTE ->> Tool calling is a MODEL capability, not a provider feature: llama3:8b
NOTE ->> answers /api/chat with `does not support tools (status code: 400)`.
NOTE ->> Read capabilities from /api/tags and refuse early with a reason the
NOTE ->> registry can surface, rather than discovering it mid-turn.
"""
