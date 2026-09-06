# LangGraph Design

**Part of the [SERA Agent implementation plan](README.md).**

---

## 6. LangGraph design

Verified against the installed versions: `langgraph 1.2.11`, `langgraph-prebuilt 1.1.0`,
`langgraph-checkpoint 4.2.0`, `langchain 1.3.15`, `langchain-core 1.6.0`.

### 6.1 Which API to use

`langchain.agents.create_agent` is the LangChain 1.x entry point (confirmed in
`langchain/agents/factory.py`). It accepts `model`, `tools`, `system_prompt`, `middleware`,
`response_format`, `context_schema`, `checkpointer`, and `cache`.

**Use both, for different jobs:**

- **`StateGraph`** for the top-level graph. The classify → guard → retrieve → generate path is a
  fixed pipeline, not an agent loop. Expressing it as a graph gives explicit parallelism and
  zero wasted LLM calls.
- **`create_agent`** for the `AGENTIC` subgraph only, where you genuinely want a tool loop and
  the middleware stack that comes with it.

Do not build the whole system as one `create_agent`. That forces every request through a tool
loop and gives up the `DIRECT` / `RAG` fast paths — the entire latency thesis.

### 6.2 Speed checklist

Ordered by expected impact.

| # | Practice | Why | Est. saving |
|---|---|---|---|
| 1 | **Compile the graph once** in lifespan; cache the module-level singleton | compilation is not free | 5–20 ms/req |
| 2 | **Pool provider clients** — never construct a chat model per request | TLS + pool setup dominates | 50–300 ms/req |
| 3 | **`durability="exit"`** for normal turns (`Durability = Literal["sync","async","exit"]`, verified) | `"sync"` writes a checkpoint at every superstep — a DB round-trip per node | 15–60 ms/req |
| 4 | **No checkpointer at all** for stateless one-shots; pass history explicitly | zero persistence overhead | 10–40 ms/req |
| 5 | **Pre-fetch RAG as a node, not a tool** (§2.1) | removes a whole LLM round-trip | 300–1500 ms/req |
| 6 | **Parallel fan-out**: `START → [guard, embed]` concurrently; search depends only on embed | LangGraph runs same-superstep nodes concurrently | 20–80 ms/req |
| 7 | **Node cache** via `CachePolicy` — `langgraph.cache.redis` **is installed** | skips recomputation across requests | up to 100 % of the node |
| 8 | **`stream_mode="messages"`** and yield the first token immediately | perceived latency | large |
| 9 | **Emit sources before the LLM starts** as a separate SSE event | the client renders citations during generation | perceived |
| 10 | **Trim state** — never carry retrieved docs into checkpointed state after generation | serialization is O(payload) | 5–30 ms/req |
| 11 | **`ToolNode` parallel execution** for `concurrency_safe` tools | n tools in 1× latency | n×tool |
| 12 | **Bound the loop**: `ModelCallLimitMiddleware` + `ToolCallLimitMiddleware` | caps worst-case latency | tail |

### 6.3 State design

```python
class SeraState(TypedDict):
    messages:   Annotated[list[AnyMessage], add_messages]
    route:      NotRequired[Literal["direct", "rag", "agentic"]]
    query_norm: NotRequired[str]
    docs:       NotRequired[list[RetrievedDoc]]   # dropped in finalize
    sources:    NotRequired[list[SourceRef]]      # small, kept
    budget:     NotRequired[BudgetTrace]
```

Keep state **small**. Every field is serialized on every checkpoint write. `docs` holds full
passage text — carry it between `retrieve` and `generate`, then drop it in `finalize` and keep
only `sources` (ids + titles + scores). This alone can cut checkpoint payloads by 10×.

### 6.4 Middleware stack

All verified present in `langchain/agents/middleware/`:

| Middleware | Use | Lane |
|---|---|---|
| `ModelFallbackMiddleware` | provider down → next provider. **The key to "it depends only on the LLM"** — when a provider dies, degrade rather than fail | hot |
| `ModelRetryMiddleware` | transient 5xx. Cap at 1 retry with a short ceiling; retries are latency | hot |
| `ToolRetryMiddleware` | transient tool failure, `concurrency_safe` tools only | hot |
| `ToolErrorMiddleware` | turn exceptions into model-readable messages | hot |
| `SummarizationMiddleware` | long sessions. **Trigger on a token threshold, not every turn** | cold-ish |
| `ContextEditingMiddleware` + `ClearToolUsesEdit` | strip stale tool results from context — fewer tokens, lower TTFT | hot |
| `HumanInTheLoopMiddleware` | `RiskLevel.HIGH` tools | hot |
| `ModelCallLimitMiddleware` / `ToolCallLimitMiddleware` | hard ceiling on runaway loops | hot |
| `PIIMiddleware` | built-in regex redaction — pairs with Presidio in the cold lane | hot |
| `LLMToolSelectorMiddleware` | **only if** the tool count exceeds ~20. It costs an LLM call; prefer static role-based filtering | — |

Plus one custom: **`permissions.py`** using `wrap_tool_call` — this is where `tools.md`'s
`PermissionPolicy` actually lives.

```python
@wrap_tool_call
def enforce_permissions(request, handler):
    spec = REGISTRY.spec(request.tool_call["name"])
    ctx  = request.runtime.context
    if spec.roles and ctx.role not in spec.roles:
        return ToolMessage(
            content=f"Not permitted: {spec.name} requires {sorted(spec.roles)}.",
            tool_call_id=request.tool_call["id"],
        )
    if spec.risk is RiskLevel.HIGH and not ctx.confirmed(request.tool_call["id"]):
        raise Interrupt(...)          # HumanInTheLoopMiddleware picks this up
    return handler(request)
```

Note the failure mode: a permission denial returns a **`ToolMessage`**, not an exception. The
model must see "you may not do that" and recover — not have the stream die.

### 6.5 Streaming

```python
async for chunk, meta in graph.astream(
    {"messages": [...]},
    config,
    stream_mode="messages",
    durability="exit",
):
```

SSE event types: `meta` (request id, route) → `sources` (emitted the instant retrieval
finishes, **before** the LLM) → `token` × N → `tool_start` / `tool_end` → `done` / `error`.

Send a heartbeat comment every 15 s so proxies do not close idle connections during a slow
provider's think time.

---

---

← [Previous](05-tools.md) · [Index](README.md) · [Next](07-multi-agent.md) →
