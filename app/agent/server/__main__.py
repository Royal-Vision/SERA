"""Sidecar entry point -- `python -m app.agent.server`. Phase 10 · Step 11.

The NDJSON read loop and nothing else: parse a frame, hand it to session.py,
write frames back. Keeping the loop this thin is what makes the 400 ms handshake
budget measurable -- anything imported here is paid before the first frame.
"""
