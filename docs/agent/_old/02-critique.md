# Critique of the Current Build

**Part of the [SERA Agent implementation plan](README.md).**

---

## 1. Critique of the current build

Requested explicitly. Ratings are 1–10 on *fitness for a low-latency multi-provider agent*,
not on effort.

### 1.1 Scorecard

| Component | Rating | Verdict |
|---|---|---|
| [app/configs/db.py](../app/configs/db.py) | **8/10** | Best file in the repo. Async engine, pooling, `pool_pre_ping`, a real round-trip on connect, and the asyncpg `prepared_statement_cache_size=0` normalization is a genuinely sharp fix. Keep as-is. |
| [app/configs/config.py](../app/configs/config.py) | **6/10** | Clean pydantic-settings. But it is missing every var the agent needs (`QDRANT_URL`, provider base URLs, cache TTLs) and it disagrees with `redis.py`. |
| [main.py](../main.py) | **6/10** | Correct lifespan shape, metrics wired. But no DB/Redis/MinIO/CORS wiring, no exception handlers, no warmup. |
| [app/blueprints/old/vector_store/rag_pipeline.py](../app/blueprints/old/vector_store/rag_pipeline.py) | **5/10** | The retrieval logic is sound and the MLflow spans are placed correctly. The *shape* is wrong for serving — see 1.2. |
| [app/configs/redis.py](../app/configs/redis.py) | **3/10** | Broken. Reads `settings.REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD` — none of which exist in `config.py`. Also `_lock = asyncio.Lock()` is created at class-definition time, binding it to whichever event loop imports the module first. |
| [app/configs/minio_bucket.py](../app/configs/minio_bucket.py) | **3/10** | Broken. Reads `settings.MINIO_*`; config defines `S3_*`. |
| [app/blueprints/old/utilities/PII.py](../app/blueprints/old/utilities/PII.py) | **3/10** | Runs a Presidio scrub **at import time**, over a hardcoded patient record (name, DOB, Emirates ID, passport, phone, email) committed to git. Two separate problems: the import side-effect, and PII-shaped data in source control. |
| [app/blueprints/old/models/rewriter_llm.py](../app/blueprints/old/models/rewriter_llm.py) | **2/10** | Loads Qwen3-4B **and runs inference** at import. Any module importing this pays several GB of VRAM and seconds of latency. |
| [app/blueprints/agent/routes.py](../app/blueprints/agent/routes.py) | **2/10** | A spike, not a foundation. See 1.3. |
| Repo hygiene | **4/10** | `mlflow.db` (712 KB), `image.png` (227 KB), `medical_o1_sft.json`, and `__pycache__/main.cpython-313.pyc` are all tracked. That `.pyc` is **cpython-313** while `pyproject.toml` pins `==3.14.7`. |
| [pyproject.toml](../pyproject.toml) | **4/10** | `requires-python = "==3.14.7"` — an exact pin on a bleeding-edge Python, combined with torch cu126 + FlagEmbedding + transformers, is a fragile stack. `langgraph` is used but undeclared (it arrives transitively via `langchain`). And torch/transformers/accelerate in the API dependency set means the API image carries multi-GB of ML deps it may never call. |

**Overall: 4.5 / 10.**

The instincts are good — singletons, Prometheus, MLflow tracing, async DB, a real eval
harness. That is more infrastructure maturity than most projects have at this stage. The
problem is that **nothing is wired and the serving path is synchronous.** Right ingredients,
wrong assembly.

### 1.2 The single biggest problem: sync code in an async server

`RagPipeline.retrieve()` and `re_rank()` are `def`, not `async def`. They call:

- `BGEM3FlagModel.encode()` — a blocking torch forward pass
- `QdrantClient.query_points()` — the **synchronous** client (blocking socket I/O)
- `self.ranker.rerank()` — a blocking cross-encoder forward pass

If any of these is called from a FastAPI `async def` route, it **blocks the entire event
loop**. One user's rerank freezes every other user's stream. Under concurrency this does not
degrade gracefully — it collapses. This is the difference between "fast for me in Postman"
and "fast in production," and it must be fixed before anything else in this document matters.

### 1.3 The current agent route

```python
@router.get("/bot")
async def stream_llm(prompt: str = Query(...)):
    llm = ChatOllama(model="gpt-oss-safeguard:latest", temperature=0)
```

Six problems in six lines:

1. **`ChatOllama` constructed per request** — new `httpx` client, new connection pool, new TLS
   handshake. Costs 50–300 ms *before the model is even asked anything*. This alone blows the
   budget.
2. **`GET` with the prompt in the query string** — prompts land in access logs, proxy caches,
   and browser history, and die at the ~2 KB URL limit. For a medical product this is a
   privacy defect, not a style nit.
3. **Model hardcoded** — no provider selection, which is the entire stated requirement.
4. **No auth, no session, no persistence, no tools.**
5. **`media_type='text/plain'`** — not SSE. No event framing, so the client cannot distinguish
   tokens from sources from errors, and there is no way to send metadata mid-stream.
6. **No error handling** — if Ollama is down, the generator raises mid-stream and the client
   receives a truncated `200`.

### 1.4 A structural critique of `tools.md`

`tools.md` is a faithful port of Claude Code's `Tool.ts`, and as a *conceptual* model it is
correct: schema → validate → authorize → execute → result. **Keep all of those concepts.**

But do not implement its `execute_tool_call` executor. LangGraph already ships that exact loop
as `ToolNode`, with parallel tool execution, error handling, state injection, and streaming
already solved. Building a parallel executor means reimplementing — and then debugging —
machinery you already have, and it will not compose with middleware or checkpointing.

**The mapping to use instead:**

| `tools.md` concept | SERA implementation |
|---|---|
| `Tool` protocol | `@tool`-decorated function + a `ToolSpec` metadata record |
| Zod `inputSchema` | Pydantic v2 `args_schema` (`model_json_schema()` for free) |
| `ToolContext` / `ToolUseContext` | `ToolRuntime` — *verified present* in `langgraph.prebuilt.tool_node`; carries `state`, `config`, `context`, `store`, `tool_call_id`, `stream_writer` |
| `PermissionPolicy.authorize()` | a `wrap_tool_call` middleware + `HumanInTheLoopMiddleware` for confirmations |
| `ToolResult` | `ToolMessage`, or `Command` when the tool must also update state |
| `is_read_only` / `is_concurrency_safe` | fields on `ToolSpec` — they drive permissions, caching, **and** parallel dispatch |
| `execute_tool_call` | `ToolNode` (do not rewrite) |
| `ToolRegistry.api_definitions()` | `ToolRegistry.for_role(role)` → the per-request tool subset |

The one thing `tools.md` has that LangGraph does not give you for free is the
**`is_read_only` / `is_concurrency_safe` metadata**. That is the valuable part — keep it,
because §6.4 uses it to decide what can run in parallel and what can be cached.

---

---

← [Previous](01-latency-contract.md) · [Index](README.md) · [Next](03-architecture.md) →
