# Provider Abstraction

**Part of the [SERA Agent implementation plan](README.md).**

---

## 8. Provider abstraction

### 8.1 The requirement

Sign in with Codex, Antigravity, or Ollama; SERA's overhead is identical across all three.

Codex and Antigravity both speak the OpenAI wire format, so `ChatOpenAI` with a custom
`base_url` covers both. Ollama gets a dedicated adapter because its performance knobs differ.

### 8.2 Registry

```python
@dataclass(frozen=True)
class ProviderSpec:
    id: str                        # "ollama" | "codex" | "antigravity"
    kind: Literal["openai_compat", "ollama"]
    base_url: str
    default_model: str
    supports_tools: bool           # gates the AGENTIC route
    supports_streaming: bool
    timeout_s: float
    max_connections: int
```

`ProviderRegistry.get(provider, model)` returns a **cached** `BaseChatModel`, keyed
`(provider, model, streaming)`, built over a **shared `httpx.AsyncClient`** with `http2=True`
and `limits=Limits(max_keepalive_connections=32, keepalive_expiry=90)`. Keepalive is what
eliminates the per-request TLS handshake.

### 8.3 Ollama specifics

The largest latency cliff in local inference is model eviction from VRAM. Set:

- **`keep_alive="30m"`** (or `-1` on a dedicated box) — without it Ollama unloads after 5 min
  and the next request pays a multi-second reload.
- **`num_ctx`** — set it to what you actually need. Ollama allocates the KV cache for the full
  context window up front; an unnecessary 32 k window is wasted VRAM and slower prefill.
- **`num_predict`** — a ceiling, so a runaway generation cannot hold a connection open forever.
- **Warm at startup** — issue a 1-token generation per configured model in lifespan.

### 8.4 Tool support is not universal

If `supports_tools` is false, the `AGENTIC` route must be unavailable for that provider — fall
back to `RAG` and say so in the response metadata. Silently dropping tool calls produces
confidently wrong answers, which in a medical product is the worst possible failure.

### 8.5 Health and fallback

Probe each provider every 30 s in the cold lane and cache the result. On sign-in the client
gets the live list, so a user never selects a dead provider. `ModelFallbackMiddleware` handles
mid-request death.

---

---

← [Previous](07-multi-agent.md) · [Index](README.md) · [Next](09-phases.md) →
