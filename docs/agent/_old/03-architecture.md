# Target Architecture

**Part of the [SERA Agent implementation plan](README.md).**

---

## 2. Target architecture

```
                         ┌──────────────── HOT LANE (blocks first token) ───────────────┐
                         │                                                               │
POST /v1/agent/chat ──► auth ──► cache probe ──► classify ──┬─► DIRECT ──────────────────┼──► LLM stream ──► SSE
   (SSE)                (JWT,    (Redis,        (regex +    │                            │
                         no DB)   semantic)      heuristic) ├─► RAG ──► embed ─┐         │
                                                            │                  ├─rerank──┤
                                                            │         search ──┘         │
                                                            └─► AGENTIC ──► tool loop ───┘
                                                                                │
                         └──────────────── COLD LANE (never blocks) ────────────┼────────┘
                                                                                ▼
                                            persist messages · MLflow spans · Prometheus ·
                                            deep PII audit · cache warm · feedback rollup
```

### 2.1 Three routes, three costs

The most expensive mistake in agent design is **making RAG a tool.** If retrieval is a tool,
every question costs a minimum of two LLM round-trips: one for the model to decide to call
`rag_search`, and one to answer with the results. On a 700 ms-TTFT provider that is 1.4 s
before the first useful token.

Instead, classify first and pre-fetch:

| Route | LLM round-trips | When | Budget (excl. LLM) |
|---|---|---|---|
| `DIRECT` | **1** | greetings, meta-questions, follow-ups answerable from history | ~15 ms |
| `RAG` | **1** | medical questions — retrieval runs *before* the LLM, docs pre-injected | ~90 ms |
| `AGENTIC` | 2+ | multi-step work, `dosage_calculator`, doc management, admin ops | ~90 ms + n×LLM |

`RAG` is the default and it costs **one** LLM call, exactly like `DIRECT`. Retrieval happens
while the user is waiting on nothing else, so it is nearly free relative to the LLM.

### 2.2 The classifier must not be an LLM

Using an LLM to route defeats the purpose. Use, in order, stopping at the first hit:

1. **Explicit override** — the client sends `mode: "agentic"`.
2. **Regex / keyword** — admin verbs (`upload`, `index`, `delete`, `run eval`) → `AGENTIC`;
   greetings and short acknowledgements → `DIRECT`. Cost: ~0.05 ms.
3. **Embedding-vs-centroid** — cosine of the query embedding against a precomputed centroid of
   the medical corpus. You are *already computing that embedding for retrieval*, so this is
   free. Below threshold → `DIRECT`.
4. **Default** → `RAG`.

Only if that proves insufficient in evals do you add a small LLM router — and then it runs on
the fastest local model, never on the user's provider.

---

## 3. Two-lane execution

**Hot lane** = anything the first token depends on. **Cold lane** = everything else.

| Work | Lane | Why |
|---|---|---|
| JWT verify | hot | signature-only, no DB lookup — ~0.2 ms |
| Redis cache probe | hot | ~1 ms, and it can skip the entire pipeline |
| PII regex fast-path | hot | raw PII must never reach a third-party provider |
| Presidio deep scan | **cold** | spaCy NER is 80–300 ms. Audit after the fact; alert on divergence from the regex pass |
| embed → search → rerank | hot | the answer depends on it |
| LLM stream | hot | this is the part the user is *supposed* to feel |
| persist session + messages | **cold** | the user does not wait on a DB write |
| MLflow span export | **cold** | remote HTTPS to `mlflow.ghoniem.online` — never inline |
| Prometheus | **cold** | in-process counters, flushed on scrape |
| cache write-back | **cold** | |

### 3.1 Implementing the cold lane

Do **not** use `BackgroundTasks`. FastAPI runs those after the response but still inside the
request task, and for a `StreamingResponse` that means after the stream closes while the
connection is held open.

Use a **bounded worker queue** owned by the app lifespan:

```python
# app/agent/runtime/cold_lane.py
class ColdLane:
    def __init__(self, workers: int = 4, maxsize: int = 10_000): ...
    def submit(self, coro_factory) -> None:               # non-blocking; drops + counts when full
    async def start(self) -> None: ...                    # spawn N workers
    async def drain(self, timeout: float = 5.0) -> None:  # called on lifespan shutdown
```

Rules: `submit()` must never `await`, never raise, and must increment
`sera_cold_lane_dropped_total` rather than block when the queue is full. Backpressure on the
cold lane must never become backpressure on the user.

### 3.2 MLflow must be async

MLflow tracing to a remote server is a network hop per span. With `@mlflow.trace` on
`retrieve` and `re_rank`, that is two synchronous HTTPS calls inside the hot path.

Enable async logging at startup and verify it:

```python
mlflow.config.enable_async_logging(True)
```

**Verify this empirically** — time the pipeline with tracing on and off. If the delta exceeds
~5 ms, the exporter is still inline and you must move span export into the cold lane manually.

---

---

← [Previous](02-critique.md) · [Index](README.md) · [Next](04-file-structure.md) →
