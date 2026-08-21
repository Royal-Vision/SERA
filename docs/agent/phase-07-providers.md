# Phase 07 — Providers

**Effort:** 1 day · **Depends on:** [02](phase-02-tool-contract.md)

---

## 1. Why this phase exists

Provider neutrality is the product premise: sign in with Codex, Antigravity or Ollama,
and SERA's overhead is identical. [Phase 00](phase-00-architecture.md) §2 identified it
as one of three competitive openings.

It is also the phase with the single largest latency mistake available. This is a real
pattern, from this repo's own first draft:

```python
@router.get("/bot")
async def stream_llm(prompt: str = Query(...)):
    llm = ChatOllama(model="gpt-oss-safeguard:latest", temperature=0)   # ← per request
```

Constructing a chat model per request creates a new `httpx` client, a new connection
pool and a new TLS handshake: **50–300 ms before the model is asked anything.** That is
the entire `turn_overhead_p95` budget, spent on setup.

---

## 2. The architecture decision

### Two adapters, three providers

Codex and Antigravity both speak the OpenAI wire format, so they share one adapter and
differ only by `base_url`. Ollama gets its own, because `keep_alive` and `num_ctx` have
no OpenAI analogue — and those two knobs are the difference between a warm local model
and a multi-second reload.

```python
@dataclass(frozen=True, slots=True)
class ProviderSpec:
    id: str                       # "ollama" | "codex" | "antigravity"
    kind: Literal["ollama", "openai_compat"]
    base_url: str
    default_model: str
    supports_tools: bool = True
    api_key_setting: str | None = None
```

### Cached construction is the whole point

```python
@lru_cache(maxsize=16)
def get_model(provider: str, model: str | None = None, temperature: float = 0.0): ...
```

Keyed on `(provider, model, temperature)`. The second call reuses the client **and its
connection pool**, which is what eliminates the per-request TLS handshake.

Build over a shared `httpx.AsyncClient`:

```python
httpx.AsyncClient(
    http2=True,
    limits=httpx.Limits(max_keepalive_connections=32, keepalive_expiry=90),
)
```

Keepalive is what makes the second request fast. HTTP/2 lets concurrent requests share
one connection.

### Lazy imports

`langchain_ollama` (~251 ms) and `langchain_openai` are imported **inside** `get_model`,
not at module scope. That keeps `providers/base.py` on the fast path so a `doctor` frame
can list providers without paying for any of them.

---

## 3. Ollama specifics

The largest latency cliff in local inference is **model eviction from VRAM**.

| Setting | Value | Why |
|---|---|---|
| `keep_alive` | `"30m"` (or `-1` on a dedicated box) | Without it Ollama unloads after ~5 minutes, and the next call pays a multi-second reload. **This is the single most important setting in the phase.** |
| `num_ctx` | what you actually need | Ollama allocates the KV cache for the full window **up front**. An unnecessary 32k window is wasted VRAM and slower prefill |
| `num_predict` | a ceiling | Stops a runaway generation holding the stream open forever |

**Warm at startup.** Issue a 1-token generation per configured model during the
handshake, so the first real prompt does not pay the load.

---

## 4. `supports_tools` is not universal

Many Ollama models have no native tool-calling endpoint. The failure mode is nasty: the
model ignores the tool schemas, answers from memory, and sounds confident.

**Never silently drop tool calls.** Three honest options:

1. Refuse the model, naming the problem
2. Fall back to the text protocol ([Phase 13](phase-13-deferred.md))
3. Run tool-free and say so in the response metadata

The `doctor` frame should report `supports_tools` per provider so the Ink frontend can
grey out models that will not work.

---

## 5. Health probes

```python
async def health(provider: str) -> tuple[bool, str]:
    url = f"{base}/api/tags" if kind == "ollama" else f"{base}/models"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(url)
        return resp.status_code < 500, f"HTTP {resp.status_code}"
    except Exception as exc:
        return False, type(exc).__name__
```

**A probe must never raise.** It returns a tuple; a dead provider is data, not an
exception.

**`< 500`, not `== 200`.** A `401` means the service is up and the key is wrong — a very
different problem from "unreachable", and the user needs to be told which.

Probe on a 30 s cycle in the background, cache the result, and serve the cache to
`doctor`. Never probe on the critical path.

A real `doctor` response from this machine:

```
  *up   ollama         http://localhost:11434     HTTP 200
        models: gemma3:4b, gpt-oss-safeguard:latest, qwen3.8:27b
   up   codex          https://api.openai.com/v1  HTTP 401
   down antigravity    (unset)                    no base_url configured
```

Three states, three different user actions: use it, fix your key, configure it.

> **Note:** `gpt-oss-safeguard` in that list is a *safety classifier*, not a chat model.
> It is the right model for [Phase 12](phase-12-guardrails.md) Tier 3 and the wrong one
> for generation — do not let it appear in a model picker.

---

## 6. Fallback

When a provider dies mid-turn, degrade rather than fail. `ModelFallbackMiddleware` exists
in LangChain but attaches to `create_agent`, which we are not using
([Phase 08](phase-08-langgraph.md) §9), so port the behaviour into the model node:

- Try the selected provider
- On connection error or 5xx, try the next **healthy** provider
- Emit a frame telling the user the switch happened — silently changing models is worse
  than failing, because output quality changes for no visible reason
- Never fall back **into** a provider whose `supports_tools` is false mid-tool-loop

Cap retries at 1 with a short ceiling. Retries are latency.

---

## 7. Credentials

Per-user BYO keys, matching "sign in with Codex/Antigravity".

- Encrypt at rest
- **Never log them** — not in tracebacks, not in `doctor` output, not in the session log
- Redact before any frame leaves the process
- A `401` reports "authentication failed", never the key prefix

---

## 8. Gate

- [ ] Second `get_model()` call for the same key: **0 ms** construction
- [ ] `doctor` returns all three providers with live status and `supports_tools`
- [ ] Ollama models list populated when Ollama is up
- [ ] `import app.agent.providers.base` does **not** import `langchain_ollama`
- [ ] A killed provider reports down within one probe interval
- [ ] Mid-turn provider death → fallback with a visible frame, no crash
- [ ] Missing `base_url` → clear config error, not a stack trace
- [ ] No credential appears in any log or frame

---

← [Previous: Phase 06 — Mutation Tools](phase-06-mutation-tools.md) · [Index](README.md) · [Next: Phase 08 — LangGraph](phase-08-langgraph.md) →
