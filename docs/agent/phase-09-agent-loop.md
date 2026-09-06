# Phase 09 — The Agent Loop

**Effort:** 2 days · **Depends on:** [08](phase-08-langgraph.md)
**The first phase where the product exists.**

---

## 1. Why this phase exists

Everything so far has been components. This is where a prompt goes in, tools run, files
change, and tokens come out — and where the `roundtrips ≤ 4` budget from
[Phase 00](phase-00-architecture.md) is either met or missed.

It is also where you find out whether the earlier phases were right. A tool engine with
90% repair rate is a number on a benchmark until an actual model, on an actual repo,
fixes an actual bug.

---

## 2. The architecture decision

### The system prompt is a component, not a string literal

It is the highest-leverage text in the system, and it is the cheapest thing to change.
Every rule in it exists to prevent a specific observed failure:

```
You are SERA, a coding agent working inside a user's project.

Tools:
- Use `glob` to find files by name, `grep` to search their contents. Prefer these
  over reading files one at a time.
- You MUST `read_file` a file before you `edit_file` it.
- `edit_file` replaces exact text. Copy `old_string` byte-for-byte from what you
  read, including indentation, and include enough surrounding context to be unique.
- Request independent tool calls together in one turn; they run in parallel.

Rules:
- Be concise. Do not narrate what you are about to do; do it, then state the result.
- If a tool returns an error, read it carefully — it usually tells you exactly what
  to fix. Do not retry the identical call.
- Never invent file contents. Read first.
```

| Line | Prevents |
|---|---|
| "Prefer glob/grep over reading one at a time" | the 4-round-trip file hunt ([Phase 04](phase-04-search-tools.md) §1) |
| "MUST read before edit" | a rejected edit, which costs a round-trip |
| "byte-for-byte, unique" | the two most common `edit_file` failures |
| "Request independent calls together" | serial dispatch, wasting Phase 05's batching |
| "Do not narrate" | tokens spent on "I'll now read the file!" |
| "Do not retry the identical call" | the retry storm the circuit breaker exists to stop |

**Version it and A/B it.** Track `roundtrips` per prompt version; this is where the
cheapest wins live.

### Turn driver separate from graph

`graph/agent.py` builds and runs the graph. `server/session.py` maps graph events onto
protocol frames. Keeping them apart means the graph has no idea a frontend exists — you
can drive it from a test with no protocol at all.

---

## 3. Streaming

```python
async for chunk, meta in graph.astream(
    {"messages": messages, "steps": 0},
    {"configurable": {"agent_context": ctx}},
    stream_mode="messages",
    durability="exit",
):
```

| Graph event | Frame | Timing |
|---|---|---|
| first `AIMessage` chunk | `token` | immediately |
| entering `tools` | `tool_start` per call | **before** execution |
| `ToolOutcome` returned | `tool_end` + `ms` + `repairs` | on completion |
| `interrupt` | `permission_request` | blocks the turn |
| graph completes | `done` + `turns` + `ms` | end |

**`tool_start` before execution** is the cheapest perceived-latency win in the whole
system. The user sees "reading src/calc.py…" while it happens rather than a frozen
cursor. Nothing about actual latency changes; the experience changes completely.

**Never buffer the whole response.** Every token is forwarded as it arrives.

---

## 4. Context assembly

Which messages go into each model call is the main driver of both cost and quality.

```
[system prompt]
[…history, per Phase 10 policy…]
[human: current prompt]
[ai: tool_calls]        ← from the previous iteration
[tool: results]         ← from the previous iteration
```

Two rules that matter more than they look:

**Tool results are `ToolMessage`s, and they are data.** Never inline a tool result into a
system or human message. Structural framing is a free
[Phase 12](phase-12-guardrails.md) mitigation — file contents must never be positioned
where instructions live.

**Trim before you send, not after.** A 20 KB `grep` result from six turns ago is re-sent
on every subsequent request. That is compounding cost, and it is the single biggest
driver of a session getting slower as it goes. [Phase 10](phase-10-sessions.md) owns the
policy.

---

## 5. Cancellation

The user hits Ctrl-C. What must happen:

1. Client sends `{"type":"cancel","id":…}`
2. Server cancels the graph task
3. `asyncio.CancelledError` propagates — **tools re-raise it**, per
   [Phase 02](phase-02-tool-contract.md)
4. In-flight `write_file` calls either complete or do not start; never a half-written file
5. Server emits `done` with `cancelled: true`
6. **The process stays alive** — this is a sidecar, not a CLI

Point 4 is why `write_file` uses a single `write_bytes()` rather than a streaming write:
there is no partial state to leave behind.

---

## 6. Error handling

| Failure | Handling |
|---|---|
| Tool raises | Already contained by [Phase 02](phase-02-tool-contract.md) — becomes a `ToolResult` |
| Provider unreachable | `error` frame, `recoverable: true`, fallback per [Phase 07](phase-07-providers.md) |
| Model emits no tool calls and no content | End the turn; do not loop |
| `steps` ceiling hit | End with a `done` frame noting the ceiling — **not** an error |
| Graph raises | `error` frame, `recoverable: false`, process survives |

**The process must survive every one of these.** A sidecar that dies takes the session
with it, and the user loses their conversation for something that should have been a
message.

---

## 7. The gate — a real task

This is the acceptance test for the whole build. Seed a scratch project:

```python
# src/calc.py
def add(a, b):
    return a - b        # the bug

def mul(a, b):
    return a * b
```

Prompt: *"There is a bug in src/calc.py. Find it and fix it."*

| Requirement | Target |
|---|---|
| Locates the bug without being told the file contents | must |
| Reads before editing | must |
| Applies the fix; file on disk is correct | must |
| Round-trips | **≤ 4** |
| Works on all three providers (subject to `supports_tools`) | must |
| Handshake still < 400 ms | must |
| stdout remains valid NDJSON, including on error paths | must |
| Cancellation mid-turn leaves no partial file | must |

Run it on the **weakest** model you intend to support, not the strongest. That is where
Phase 05's repair layer proves its value — and if it passes on a local 4B model, the
competitive claim in [Phase 00](phase-00-architecture.md) §2 is real.

---

## 8. What to measure

```
sera_turn_roundtrips{provider}              ← the one that matters
sera_turn_seconds{provider, phase}          ← phase: import|model|tools|total
sera_tool_duration_seconds{tool, outcome}
sera_tool_repairs_total{tool, kind}
```

Watch `sera_tool_repairs_total` over time. A rising repair rate is the earliest signal
that a provider has changed its output format — usually before users report anything.

---

← [Previous: Phase 08 — LangGraph](phase-08-langgraph.md) · [Index](README.md) · [Next: Phase 10 — Sessions & Context](phase-10-sessions.md) →
