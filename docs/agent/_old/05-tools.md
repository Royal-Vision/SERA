# Tool Layer

**Part of the [SERA Agent implementation plan](README.md).**

---

## 5. The tool layer

### 5.1 Contracts to build first

**`app/agent/contracts.py`**

```python
class RiskLevel(StrEnum):
    SAFE      = "safe"        # read-only, no side effects   → auto-allow
    LOW       = "low"         # writes user-owned data       → allow if authenticated
    MEDIUM    = "medium"      # writes shared state          → role gate
    HIGH      = "high"        # destructive or irreversible  → human confirmation
    CLINICAL  = "clinical"    # affects clinical advice      → role gate + audit + disclaimer

@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    category: ToolCategory
    risk: RiskLevel
    read_only: bool
    concurrency_safe: bool          # → may run in the same parallel batch
    cache_ttl_s: int | None         # None = never cache
    timeout_s: float                # hard ceiling; must always be set
    roles: frozenset[str]           # {"user"} | {"admin"} | {"user","admin"}
    budget_ms: int                  # expected p95 — asserted in perf tests
```

**`SeraContext`** is the per-request context, passed via `create_agent(context_schema=...)`
and read inside tools through `runtime.context`:

```python
@dataclass
class SeraContext:
    user_id: str
    role: str
    session_id: str
    provider: str                  # "ollama" | "codex" | "antigravity"
    model: str
    locale: str                    # "ar" | "en"
    request_id: str
    deadline_at: float             # monotonic; tools must self-cancel past this
    cold: ColdLane
```

`deadline_at` matters: a tool that would exceed the request deadline should return a partial
or empty result rather than blow the budget. **Never let a slow tool become a slow product.**

### 5.2 The canonical tool shape

Every tool follows this. `ToolRuntime` injection is verified available in LangGraph 1.2.11.

```python
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime

class RagSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=3, max_length=2000, description="...")
    top_n: int = Field(default=5, ge=1, le=10)

@tool(args_schema=RagSearchInput)
async def rag_search(query: str, top_n: int, runtime: ToolRuntime[SeraContext]) -> str:
    """Search the SERA medical knowledge base. Use for any clinical question."""
    ctx = runtime.context
    ...
```

Four non-negotiable rules:

1. **`async def` always.** A sync tool blocks the event loop. If the underlying work is sync
   (torch), the tool awaits a service that thread-pools it — it never calls torch directly.
2. **`extra="forbid"` on every input model.** Models hallucinate parameters; fail loudly.
3. **Return a string shaped for a model, not a dict dumped to JSON.** Token count is latency.
   Return the 5 fields the model needs, not the 20 the DB has.
4. **Never raise across the tool boundary.** Catch, log the full traceback server-side, return
   a short actionable message. An unhandled exception in a tool kills the stream.

### 5.3 The tool catalog

Legend: **RO** read-only · **CS** concurrency-safe · budget = p95 target excluding LLM.

#### Retrieval — `tools/retrieval.py`

| Tool | Input | Returns | RO | CS | Risk | Cache | Budget |
|---|---|---|---|---|---|---|---|
| `rag_search` | `query`, `top_n`, `min_score?` | ranked passages + scores + doc ids | ✓ | ✓ | SAFE | 300 s | 90 ms |
| `rag_fetch_doc` | `doc_id`, `max_chars` | full passage text | ✓ | ✓ | SAFE | 3600 s | 15 ms |

`rag_search` is the workhorse:
- Cache key: `sha256(normalized_query + collection + top_k + top_n)`. Normalize = lowercase,
  collapse whitespace, strip punctuation. Expect a 25–45 % hit rate on medical Q&A.
- On reranker failure, fall back to vector order (the existing code already does this — keep
  it) and emit `sera_rerank_fallback_total`.
- Return `rerank_score` to the model. It helps the model hedge when the top score is weak,
  which matters in a medical context.

#### Session — `tools/session.py`

| Tool | Input | Returns | RO | CS | Risk | Cache | Budget |
|---|---|---|---|---|---|---|---|
| `session_history` | `limit`, `before?` | prior turns, compacted | ✓ | ✓ | SAFE | 60 s | 15 ms |
| `session_search` | `query`, `limit` | matching past sessions | ✓ | ✓ | SAFE | 120 s | 40 ms |
| `submit_feedback` | `message_id`, `rating`, `comment?` | confirmation | ✗ | ✗ | LOW | — | 20 ms |

`submit_feedback` **must verify `message.user_id == ctx.user_id`** inside the tool. Never trust
an id the model produced — a model can be argued into passing someone else's id. This applies
to every tool that accepts an id.

#### Clinical — `tools/clinical.py`

| Tool | Input | Returns | RO | CS | Risk | Cache | Budget |
|---|---|---|---|---|---|---|---|
| `dosage_calculator` | `drug`, `weight_kg`, `age_years`, `renal_clearance?` | dose + range + citation | ✓ | ✓ | CLINICAL | 3600 s | 5 ms |
| `drug_interaction_check` | `drugs[]` | interaction pairs + severity | ✓ | ✓ | CLINICAL | 3600 s | 30 ms |
| `unit_convert` | `value`, `from_unit`, `to_unit`, `substance?` | converted value | ✓ | ✓ | SAFE | — | 1 ms |

**These exist specifically so the LLM never does arithmetic.** A model that miscomputes a
pediatric dose is the worst failure this product can have. Deterministic table lookup and exact
arithmetic, every time, with the source cited in the output.

Every `CLINICAL` result must carry a provenance field the generation prompt is required to
surface. If you cannot cite it, do not return it.

#### Safety — `tools/safety.py`

| Tool | Input | Returns | RO | CS | Risk | Cache | Budget |
|---|---|---|---|---|---|---|---|
| `pii_scrub` | `text`, `mode` | redacted text + match count | ✓ | ✓ | SAFE | — | 3 ms (`fast`) |
| `escalate_to_clinician` | `reason`, `urgency`, `summary` | ticket id | ✗ | ✗ | HIGH | — | 30 ms |

`pii_scrub` has two modes. `fast` = compiled regex for the identifier classes that actually
matter here (Emirates ID, passport, phone, email, MRN) — this runs in the hot lane. `deep` =
Presidio — cold lane only. Reuse the existing `PIIScrubber` singleton **after** moving its demo
block into `if __name__ == "__main__":`.

`escalate_to_clinician` is `HIGH` risk and routes through `HumanInTheLoopMiddleware`.

#### Documents (admin) — `tools/documents.py`

| Tool | Input | Returns | RO | CS | Risk | Budget |
|---|---|---|---|---|---|---|
| `doc_list` | `limit`, `offset`, `q?` | document metadata | ✓ | ✓ | SAFE | 20 ms |
| `doc_upload` | `filename`, `upload_token` | doc id, MinIO uri | ✗ | ✗ | MEDIUM | 200 ms |
| `doc_index` | `doc_id`, `chunk_strategy` | job id | ✗ | ✗ | MEDIUM | 10 ms *(enqueue only)* |
| `doc_delete` | `doc_id`, `purge_vectors` | confirmation | ✗ | ✗ | **HIGH** | 50 ms |

`doc_index` **must not** parse + embed + upsert inline — that is minutes of work. It enqueues
and returns a job id immediately. Add `doc_index_status(job_id)` so the agent can poll.

Prefer `upload_token` over inline base64: base64 in a tool call means the file passes through
the model's context window, which is both absurdly slow and expensive. The real upload goes to
`POST /v1/docs/upload` and the agent receives only a token.

`doc_delete` requires human confirmation. Always.

#### Evaluation (admin) — `tools/evaluation.py`

| Tool | Input | Returns | RO | CS | Risk | Budget |
|---|---|---|---|---|---|---|
| `eval_trigger` | `dataset_version`, `scorers[]`, `sample_size` | run id | ✗ | ✗ | MEDIUM | 10 ms *(enqueue)* |
| `eval_list` | `limit`, `status?` | run summaries | ✓ | ✓ | SAFE | 100 ms |
| `eval_detail` | `run_id` | metrics + artifacts | ✓ | ✓ | SAFE | 100 ms |
| `trace_lookup` | `trace_id` | span tree summary | ✓ | ✓ | SAFE | 100 ms |

These wrap the existing `RagEvalMLflow`. Enforce a cost cap on `eval_trigger` — an LLM-judged
eval over 300 rows is real money, and an agent that *can* trigger it *will*.

#### Utility — `tools/utility.py`

| Tool | Input | Returns | RO | CS | Risk | Budget |
|---|---|---|---|---|---|---|
| `calculator` | `expression` | result | ✓ | ✓ | SAFE | 1 ms |
| `current_datetime` | `timezone?` | ISO timestamp | ✓ | ✓ | SAFE | 0.1 ms |
| `web_search` | `query`, `n` | results | ✓ | ✓ | LOW | 800 ms |

`calculator` must use a restricted AST evaluator — **never `eval()`**. Whitelist node types and
operators; reject everything else.

`web_search` is 800 ms. It belongs only in the `AGENTIC` branch, never on the default path.

### 5.4 Infrastructure functions (not exposed to the model)

These are the rest of "all the functions that need to be built."

**`services/embedding.py`**
- `async aencode(texts: list[str], *, cache: bool = True) -> list[list[float]]`
- `async aencode_one(text: str) -> list[float]` — the hot-path call
- Runs `BGEM3FlagModel.encode` under `anyio.to_thread.run_sync` with a `CapacityLimiter` sized
  to GPU capacity (start at 2). Unbounded thread dispatch will OOM the GPU.
- Micro-batching: a 5–10 ms collection window that groups concurrent single-query encodes into
  one batched forward pass. Under load this is a 3–5× throughput win for ~5 ms of added
  latency. Make the window configurable and measure before committing to it.
- Redis cache keyed `emb:v1:{sha256(text)}`, 24 h TTL, stored as raw float32 bytes — **not
  JSON**. JSON-encoding a 1024-dim vector wastes ~15 KB and ~1 ms per call.

**`services/rerank.py`**
- `async arerank(query, docs, top_n) -> list[RetrievedDoc]`
- Same thread-pool discipline. Keep the existing vector-order fallback.
- Tune `TOP_K` down. Reranking 20 docs to keep 5 is the largest fixed cost after the LLM;
  measure recall@5 at `TOP_K` ∈ {20, 12, 8} on the frozen eval set and pick the smallest
  `TOP_K` that holds recall. This is likely the highest-value latency experiment available.

**`services/vector.py`**
- Wrap **`AsyncQdrantClient`**, not `QdrantClient`. Non-negotiable.
- One client for the app lifetime, created in lifespan, `prefer_grpc=True` if the deployment
  allows — gRPC saves roughly 5–15 ms per query versus HTTPS.

**`services/cache.py`**
- `async get_embedding` / `set_embedding`
- `async get_retrieval` / `set_retrieval`
- `async get_answer` / `set_answer` — exact-match answer cache keyed by
  `(normalized_query, provider, model, top_n)`. On a hit the whole pipeline is skipped and TTFT
  is ~5 ms. Include `provider` and `model` in the key, or users will see another provider's
  answers.
- Every method must degrade to a miss on Redis failure. **Cache errors must never fail a
  request.**

**`providers/base.py`**
- `ProviderRegistry.get(provider, model, *, streaming=True) -> BaseChatModel` — returns a
  **cached, warm** instance. This is the fix for critique 1.3.1.
- `list_available() -> list[ProviderInfo]`
- `async health(provider) -> ProviderHealth`

**`runtime/cold_lane.py`** — §3.1.

**`graph/build.py`** — `build_graph()` called **once** in lifespan, the compiled graph cached at
module level. Compiling per request is 5–20 ms of pure waste.

---

---

← [Previous](04-file-structure.md) · [Index](README.md) · [Next](06-langgraph.md) →
