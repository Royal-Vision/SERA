"""Provider protocol -- Phase 07 · Step 9.

The seam that keeps model choice free (Decision 6, Phase 00 §7): guardrails and
tool semantics live in the harness, so a provider only has to turn messages plus
tool schemas into either text or tool calls.

Native tool-calling is required for v1 (Decision 7). The text-protocol fallback
that unlocks non-tool-calling local models is Phase 13, and the shape of THIS
protocol decides whether that fallback is an adapter or a rewrite.

NOTE ->> A stub provider that replays a fixed tool-call script instantly is not
NOTE ->> a test convenience, it is the benchmark harness: measuring against a
NOTE ->> real LLM measures the LLM, not you (Phase 00 §5).
"""
