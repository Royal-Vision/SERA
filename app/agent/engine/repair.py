"""Argument repair -- Phase 05. Pure functions, no I/O, no context.

Small models emit `{'path': 'x.py',}` -- single quotes, trailing comma. Every
malformed payload that reaches validation unrepaired costs a full round-trip to
re-ask, and roundtrips <= 4 is the budget everything else defers to
(Phase 00 §5).

Three jobs, in order: JSON recovery, type coercion against the input model,
fuzzy resolution of a tool name that nearly matches a canonical one
(see aliases() in tools/__init__.py -- aliases are for OLD transcripts and are
a different mechanism from a misspelling).

Built first because it is pure: every fixture in test_repair.py comes from real
output of a real small model, which BUILD-ORDER calls the most valuable asset
the test suite acquires.
"""
