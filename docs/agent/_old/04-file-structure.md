# File Structure

**Part of the [SERA Agent implementation plan](README.md).**

---

## 4. File structure

```
app/
├── agent/                          ← NEW — everything agent-related
│   ├── __init__.py
│   │
│   ├── contracts.py                ← ToolSpec, ToolCategory, RiskLevel, SeraContext
│   ├── registry.py                 ← ToolRegistry: register / for_role / json_schemas
│   ├── errors.py                   ← ToolError hierarchy → safe model-facing messages
│   │
│   ├── tools/                      ← one file per tool family
│   │   ├── __init__.py             ← imports all, builds the singleton registry
│   │   ├── retrieval.py            ← rag_search, rag_fetch_doc
│   │   ├── session.py              ← session_history, session_search, submit_feedback
│   │   ├── clinical.py             ← dosage_calculator, drug_interaction_check, unit_convert
│   │   ├── safety.py               ← pii_scrub, escalate_to_clinician
│   │   ├── documents.py            ← doc_upload, doc_index, doc_list, doc_delete
│   │   ├── evaluation.py           ← eval_trigger, eval_list, eval_detail, trace_lookup
│   │   └── utility.py              ← calculator, current_datetime, web_search
│   │
│   ├── graph/
│   │   ├── __init__.py
│   │   ├── state.py                ← SeraState (TypedDict) + reducers
│   │   ├── build.py                ← build_graph() → compiled singleton
│   │   ├── nodes/
│   │   │   ├── classify.py         ← non-LLM router
│   │   │   ├── guard.py            ← PII fast-path + injection screen
│   │   │   ├── retrieve.py         ← embed ‖ search → rerank
│   │   │   ├── generate.py         ← streaming LLM call
│   │   │   └── finalize.py         ← cold-lane dispatch
│   │   └── subgraphs/
│   │       ├── clinical.py         ← agentic branch: clinical tools
│   │       └── admin.py            ← agentic branch: docs + eval
│   │
│   ├── middleware/
│   │   ├── permissions.py          ← wrap_tool_call → RBAC + risk gate
│   │   ├── observability.py        ← span + metric emission (cold lane)
│   │   └── budget.py               ← per-request token/time/tool-call ceilings
│   │
│   ├── providers/
│   │   ├── __init__.py
│   │   ├── base.py                 ← ProviderSpec, ProviderRegistry
│   │   ├── ollama.py               ← keep_alive, num_ctx, warm pull
│   │   ├── openai_compat.py        ← Codex / Antigravity / any OpenAI-shaped endpoint
│   │   └── warmup.py               ← lifespan prewarm + health probes
│   │
│   ├── services/                   ← sync-heavy work, isolated behind async facades
│   │   ├── embedding.py            ← aencode() — thread-pooled + batched + cached
│   │   ├── rerank.py               ← arerank() — thread-pooled + cached
│   │   ├── vector.py               ← AsyncQdrantClient wrapper
│   │   └── cache.py                ← Redis: embeddings, retrieval, answers
│   │
│   ├── runtime/
│   │   ├── cold_lane.py            ← §3.1
│   │   ├── threadpool.py           ← bounded anyio CapacityLimiter per model
│   │   └── budget.py               ← latency budget accounting + SLO metrics
│   │
│   └── api/
│       ├── routes.py               ← POST /v1/agent/chat (SSE), /providers, /health
│       ├── schemas.py              ← ChatRequest / ChatEvent / ProviderInfo
│       └── sse.py                  ← event framing + heartbeat + error frames
│
└── configs/                        ← existing; see Phase 0 fixes
```

---

---

← [Previous](03-architecture.md) · [Index](README.md) · [Next](05-tools.md) →
