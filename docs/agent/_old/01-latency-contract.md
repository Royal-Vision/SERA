# Latency Contract

**Part of the [SERA Agent implementation plan](README.md).**

---

## 0. The one design constraint

> When a user signs in with Codex / Antigravity / Ollama, **the only thing they should
> feel is their own LLM.** Everything SERA adds must be invisible.

That is a measurable constraint, not a slogan. It becomes the **system overhead budget**:

| Metric | Definition | Target |
|---|---|---|
| `sys_overhead_p50` | time from HTTP request → first byte of the LLM prompt being sent, **plus** all post-stream work that blocks the connection | **≤ 120 ms** |
| `sys_overhead_p95` | same | **≤ 300 ms** |
| `ttft_total` | request → first token at the client | `sys_overhead + provider_ttft` |
| `post_stream_block` | time after the last token before the connection closes | **≤ 5 ms** |

Every decision in this document is justified against that budget. If a feature cannot fit,
it moves to the **cold lane** (§3) or it does not ship.

---

---

[Index](README.md) · [Next](02-critique.md) →
