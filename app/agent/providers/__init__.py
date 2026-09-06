"""Model providers -- L3. Phase 07 · Step 9. Warm client registry.

One client per provider, created once and reused. A cold client per turn pays
TLS and pool setup on a path already budgeted at roundtrips <= 4, and provider
churn is the top defect in the old blueprints/agent/routes.py.

Import rule: providers/ knows about contracts and messages. It does NOT know
about graph/ -- swapping ollama for openai_compat must not touch the graph.
"""
