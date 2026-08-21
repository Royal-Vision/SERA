# Implementation Phases

**Part of the [SERA Agent implementation plan](README.md).**

Scope: **the Python agent backend** for a coding agent — the thing `docs/tools.md`
describes, with Codex / Antigravity / Ollama as interchangeable back ends.

**The CLI frontend is React Ink (Node/TS) and is built separately.** Python owns the
tool engine, permission policy, provider abstraction and agent loop, and speaks NDJSON
over stdio. Phase 0 defines that boundary.

Each phase is independently shippable and ends with a **measurable gate**. Do not start
a phase before its predecessor's gate passes. The gates are the point: they are what
stop the latency contract from eroding one convenient shortcut at a time.

> **Note on ordering.** This sequence follows the build order in `docs/tools.md` §Build
> order, with one deviation: the tool *engine* (Phase 3) lands before the mutation tools
> (Phase 4). `docs/tools.md` puts write/edit earlier. Reason for the change: mutation
> tools are where a malformed argument does permanent damage, so the repair and
> precondition layers should exist before anything can write to disk.

---

## Phase 0 — Runtime skeleton

**Goal:** a backend process that starts fast, speaks a stable protocol, and proves the
import discipline before it can do anything at all.

This phase looks trivial and is not. Import cost is the dominant startup latency, and
it is nearly impossible to retrofit — by the time twenty modules exist, something
imports LangGraph at module scope and the 1.8 s is baked in permanently.

**Deliverables**

| Module | Contents |
|---|---|
| `app/agent/perf.py` | `apply_performance_mode()`, `dumps`/`loads`, `compress`/`decompress`, `pack_vector`/`unpack_vector`, `new_id()`, `enable_eager_tasks()`, `freeze_after_warmup()`, `tune_gc()`, `configure_stdio()` |
| `app/agent/server/__main__.py` | Process entry point — reads NDJSON requests on stdin, writes NDJSON events on stdout |
| `app/agent/server/protocol.py` | Request/event envelope schemas, `ready` handshake |
| `scripts/bench_runtime.py` | The measurement harness for every claim in [11-tool-engine.md](11-tool-engine.md) §8 |

> **The CLI itself is React Ink (Node/TS) and is out of scope for these phases.** Python
> is the agent backend. See §Transport below for the boundary contract.

**Rules established here, enforced forever after**

1. `perf.py` imports **stdlib only**. No pydantic, no langchain, no langgraph.
2. Every `langgraph` / `langchain` import is **function-local**, never module scope.
3. `configure_stdio()` runs before any write. Windows consoles are cp1252 and raise
   `UnicodeEncodeError` on non-Latin-1 bytes — and here it would corrupt the protocol
   stream, not just the display.
4. **stdout carries protocol frames only.** Every log line, traceback and diagnostic
   goes to stderr. A stray `print()` on stdout desynchronises the Ink client.

**Transport: newline-delimited JSON over stdio**

Recommended over a local HTTP server: no port, no auth, no CORS, no TLS handshake, and
the process lifetime is owned by the parent. One JSON object per line, both directions.

```
→ {"id":"01J...","type":"prompt","text":"fix the bug in calc.py","cwd":"/proj","mode":"default"}
← {"id":"01J...","type":"ready"}
← {"id":"01J...","type":"token","text":"Looking"}
← {"id":"01J...","type":"tool_start","tool":"read_file","args":{...}}
← {"id":"01J...","type":"tool_end","tool":"read_file","ok":true,"ms":3.1,"repairs":[]}
← {"id":"01J...","type":"permission_request","tool":"bash","key":"bash(rm -rf build)","risk":"high"}
→ {"id":"01J...","type":"permission_response","decision":"allow_once"}
← {"id":"01J...","type":"done","turns":3,"ms":4120}
```

Use `perf.dumps` (orjson) for framing — measured 77% faster than stdlib `json`.

**Gate**

```
cold start → "ready" frame       < 400 ms   (langgraph import dominates; see below)
"langgraph" in sys.modules at handshake  →  False
"torch"     in sys.modules ever          →  False
stdout contains only valid NDJSON        →  always
```

**Why the import discipline still matters without a CLI.** If Ink spawns the backend
per invocation, `import langgraph.graph` (~1800 ms measured) is paid on every command.
Two ways out, and you should pick one now:

- **Persistent sidecar (recommended).** Ink spawns the Python process once and keeps it
  alive. Import cost is paid once at startup, behind the Ink splash. Warm the graph
  import in a background thread *during* the handshake so it is ready before the first
  prompt arrives.
- **Per-invocation spawn.** Then the lazy-import rules are load-bearing exactly as they
  would be for a CLI, and you must keep the graph off the handshake path.

**Effort:** 0.5 day.

---

## Phase 1 — Tool contract + first tool

**Goal:** the full `validate → authorize → execute → ToolResult` loop working end to
end, on the tool with the smallest security surface.

**Deliverables**

| Module | Contents |
|---|---|
| `app/agent/contracts.py` | `ToolResult`, `ToolSpec`, `RiskLevel`, `PermissionMode`, `PermissionContext`, `PermissionResult`, `Decision`, `AgentContext` |
| `app/agent/base.py` | `Tool` ABC, `PermissionPolicy`, `ToolRegistry`, `build_default_registry()` |
| `app/agent/tools/read.py` | `ReadFileTool` |

**Design decisions to lock in now** (changing them later is expensive)

- **`extra="forbid"` on every input model.** Models hallucinate parameters constantly.
  Silent acceptance produces wrong behaviour; loud rejection produces a correction.
- **`ToolSpec.read_only` and `.concurrency_safe` are load-bearing**, not documentation.
  Phase 3 uses them to decide what runs in parallel. Get them right per tool.
- **`AgentContext.resolve_in_project()` is the single path chokepoint.** Every
  filesystem tool routes through it. `Path.resolve()` first, so symlinks cannot escape.
- **`budget_ms` per tool**, asserted in tests from Phase 2 onward.
- **Tools never raise.** `Tool.run()` catches everything, logs the traceback
  server-side, returns a short message to the model.

**Gate**

- `read_file` returns numbered lines, truncates over 256 KB, rejects binary (NUL byte in
  first 8 KB), and refuses `../../etc/passwd`.
- `ReadFileInput.model_json_schema()` is a valid tool schema.
- Passing an unknown parameter fails validation rather than being ignored.
- p95 ≤ `budget_ms` (25 ms) on a warm file.

**Effort:** 1 day.

---

## Phase 2 — Search tools

**Goal:** the agent can find things without reading files one at a time. This is the
single biggest determinant of how many turns a task takes.

**Deliverables**

| Module | Contents |
|---|---|
| `app/agent/tools/glob.py` | `GlobTool`, `PRUNE_DIRS` |
| `app/agent/tools/grep.py` | `GrepTool` with ripgrep fast path + pure-Python fallback |

**Requirements**

- **Prune aggressively.** `.venv`, `node_modules`, `.git`, `__pycache__`, `dist`. On
  this repo `.venv` alone holds >40 000 files; an unpruned `**/*.py` walk is the
  difference between ~40 ms and several seconds.
- **`Path.glob` cannot prune mid-walk.** Hand-roll `os.walk` and mutate `dirnames[:]`.
- **Sort newest-first.** When an agent asks "where are the route files", recently
  touched ones are almost always the relevant ones, so recency ordering means the useful
  answer survives truncation.
- **ripgrep when available, Python when not.** A CLI that only works when the user
  happens to have `rg` installed is broken for most users. Note `rg` exits **1** for
  "no matches" — that is a valid empty result, not a failure.
- **Both run in a worker thread.** `os.walk` and a regex scan block. One blocking glob
  on the event loop freezes every other concurrent tool call.

**Gate**

- `glob("**/*.py")` on this repo: p95 ≤ 120 ms, `.venv` absent from results.
- `grep` p95 ≤ 250 ms, identical results from both backends on a fixture set.
- Event loop never blocked > 50 ms (verify under `asyncio` debug mode).

**Effort:** 1 day.

---

## Phase 3 — Tool engine

**Goal:** make tool calls fail less, and make the failures that remain self-correcting.

Full design and diagrams: **[11-tool-engine.md](11-tool-engine.md)**.

**Deliverables**

| Module | Contents |
|---|---|
| `app/agent/engine/repair.py` | `RepairLog`, `repair_json()`, `coerce_to_schema()`, `resolve_tool_name()` |
| `app/agent/engine/executor.py` | `ToolEngine`, `ToolCall`, `ToolOutcome`, `Outcome`, circuit breaker, `_plan_batches()`, `_actionable_validation_error()` |
| `app/agent/engine/preconditions.py` | `FileStateTracker`, `FileSnapshot`, `tracker_for()` |

**The ten failure modes to handle** — these are what models actually emit:

| # | Failure | Repair |
|---|---|---|
| 1 | Markdown-fenced JSON | strip fence |
| 2 | Prose around the object | brace-balanced scan |
| 3 | Trailing commas | regex strip |
| 4 | Single quotes | `ast.literal_eval` |
| 5 | Truncated mid-generation | close to last complete pair |
| 6 | `"5"` for an int | schema-directed coercion |
| 7 | `"[1,2]"` for a list | parse, or wrap scalar |
| 8 | `True`/`None` | `ast.literal_eval` |
| 9 | `readfile` / `functions.read_file` | case → style → namespace → fuzzy ≥0.85 |
| 10 | `"CONTENT"` for `"content"` | enum near-match |

**Two traps worth naming explicitly**

- **Pydantic renders enums as `$ref` into `$defs`,** not as an inline `enum`. Coercion
  that only looks for `prop["enum"]` will silently miss every enum near-miss. Resolve
  `$ref`, including through `allOf` / `anyOf`.
- **A falsy log object is a bug magnet.** If `RepairLog.__bool__` returns
  `bool(self.repairs)`, then `log = log or RepairLog()` silently discards the caller's
  log and every repair goes unreported. Use `log if log is not None else RepairLog()`.

**Non-negotiables**

- **Errors are prompts.** `_actionable_validation_error()` appends the offending field's
  JSON-Schema constraints and the list of valid parameters. `"ValidationError"` teaches
  a model nothing; `"max_lines: ≤10000 — you sent 99999"` gets it right next turn.
- **Nothing escapes as an exception.** Every terminal state is a `ToolResult`.
- **Ordering is preserved.** Results come back in requested order regardless of
  completion order — models reason about tool results positionally.
- **Circuit breaker.** 3 consecutive failures → short-circuit with "try another
  approach". Without it, a model that retries a broken tool consumes the whole turn.

**Gate** — build a golden set of ~40 real malformed tool calls, then:

| Metric | Target |
|---|---|
| Recovery rate on the golden set | **≥ 90%** |
| False repairs (changed a *valid* call) | **0** |
| Unhandled exceptions escaping the engine | **0** |
| Batch planner: unsafe tool shares a batch | **never** |
| Batch planner: overlapping paths share a batch | **never** |
| Requested-order preservation | **100%** |

**Effort:** 2 days. This is the highest-value phase in the plan.

---

## Phase 4 — Mutation tools

**Goal:** the agent can change files, without any path to silent data loss.

**Deliverables**

| Module | Contents |
|---|---|
| `app/agent/tools/edit.py` | `EditFileTool` |
| `app/agent/tools/write.py` | `WriteFileTool` |

**Why exact-string replacement, not line numbers or diffs**

1. Line numbers go stale the moment anything above the edit changes.
2. A **unique-match requirement is a free correctness check** — if `old_string` appears
   twice, the model's mental model of the file is wrong, and failing loudly beats
   editing the wrong occurrence.
3. Unified diffs need fuzzy hunk matching to be usable, and fuzzy matching on source
   code silently produces wrong results.

**Requirements**

- **read-before-edit, enforced.** The state machine in [11-tool-engine.md](11-tool-engine.md) §5.
  `ReadFileTool` must call `record_read()` on the **full** bytes, before truncation, or
  the hash will not match. *(This wiring is easy to forget and the failure is silent —
  the tracker simply has no input and every edit is rejected.)*
- **Hash guard.** Cheap `size + mtime_ns` check first; SHA-256 only when ambiguous.
  Catches external processes and parallel batches modifying a file mid-turn.
- **Preserve newline convention.** Rewriting a CRLF file with LF endings produces a diff
  touching every line, which is unreviewable.
- **Overwriting an existing file requires a prior read; creating a new one does not.**
  There is nothing to lose in the second case.
- Both tools are `concurrency_safe=False` and `plan_mode_safe=False`.

**Gate**

- Editing without a prior read → refused, with an actionable message.
- Editing after an external modification → refused, with an actionable message.
- Non-unique `old_string` without `replace_all` → refused, reports the count.
- CRLF file edited → still CRLF.
- Plan mode → `write_file` and `edit_file` are **not offered** to the model at all, not
  merely denied. A model that cannot see a tool does not waste a turn on it.

**Effort:** 1 day.

---

## Phase 5 — Provider layer

**Goal:** Codex / Antigravity / Ollama behind one interface, with zero per-request
construction cost.

**Deliverables**

| Module | Contents |
|---|---|
| `app/agent/providers/base.py` | `ProviderSpec`, `get_spec()`, `list_providers()`, `get_model()` (cached), `health()` |
| `app/agent/providers/ollama.py` | Ollama-specific knobs |
| `app/agent/providers/openai_compat.py` | Codex + Antigravity |
| `app/agent/server/handlers.py` | `doctor` request type — provider health, tool list, runtime info |

**The one thing that matters most**

**Chat model instances must be cached.** Constructing one per request creates a new
`httpx` client, a new connection pool and a new TLS handshake: 50–300 ms before the
model is asked anything. `functools.lru_cache` keyed on `(provider, model, streaming)`
over a shared `httpx.AsyncClient` with `http2=True` and
`limits=Limits(max_keepalive_connections=32, keepalive_expiry=90)`.

**Ollama specifics**

- **`keep_alive="30m"`** — without it Ollama evicts the model after ~5 minutes and the
  next call pays a multi-second reload. This is the largest latency cliff in local
  inference.
- **`num_ctx`** — Ollama allocates the KV cache for the full window up front. An
  unnecessary 32 k window is wasted VRAM and slower prefill.
- **`num_predict`** — a ceiling, so a runaway generation cannot hold the stream open forever.

**Tool support is not universal.** If `supports_tools` is false, do not silently drop
tool calls — that produces confidently wrong answers. Either fall back to the text
protocol (Phase 6) or refuse the model with a clear message.

**Gate**

- Second and subsequent `get_model()` calls for the same key: **0 ms** construction.
- A `doctor` request returns every provider with live up/down status and, for Ollama,
  the installed model list — enough for Ink to render a provider picker.
- A killed provider is reported as down within one health interval.

**Effort:** 1 day.

---

## Phase 6 — The agent loop

**Goal:** end-to-end. A prompt goes in, tools run, files change, tokens stream out.

**Deliverables**

| Module | Contents |
|---|---|
| `app/agent/graph/agent.py` | `build_agent()`, `make_context()`, system prompt |
| `app/agent/server/session.py` | Turn driver — maps graph events onto protocol frames |

**Hand-built `StateGraph`, not `create_agent`**

Tool execution must go through `ToolEngine` so every call gets the repair pipeline,
permission gate, circuit breaker and conflict-aware batching. `create_agent`'s built-in
`ToolNode` bypasses all of it. The loop itself is ~15 lines:

```
START → model → (tool calls?) → tools → model → … → END
```

**Two LangGraph traps on Python 3.14** — both cost real debugging time:

1. **A `class _State(TypedDict)` declared inside a function stores its annotations as
   strings** (PEP 563/649), and LangGraph resolves them via `get_type_hints()` against
   *module* globals — where a locally-imported `add_messages` does not exist. Use the
   functional form: `TypedDict("_State", {"messages": Annotated[list, add_messages], ...})`.
2. **The same applies to the branch function.** LangGraph calls `get_type_hints()` on
   the `should_continue` callable passed to `add_conditional_edges`. If it is annotated
   `state: _State` and `_State` is function-local, this raises
   `NameError: name '_State' is not defined`. Leave the branch function's parameter
   unannotated, or annotate it `dict`.

**Performance requirements**

- Compile the graph **once**, cache it. Compiling per invocation is pure waste.
- **No checkpointer** for a single non-resumable turn — nothing to resume, and a
  checkpointer adds a write per superstep. Add `AsyncPostgresSaver` with
  `durability="exit"` only when resumable sessions land (Phase 8).
- `stream_mode="messages"`, print the first token immediately.
- Emit `token`, `tool_start` and `tool_end` frames as they happen — Ink renders tool
  activity live rather than waiting for the turn to finish. **Frames on stdout, logs on
  stderr**, always.
- `enable_eager_tasks()` at loop start, `freeze_after_warmup()` after the graph compiles.

**Gate**

Give the agent a seeded bug in a scratch project and the prompt *"There is a bug in
src/calc.py. Find it and fix it."*

- It reads before editing, applies the fix, and the file on disk is correct.
- Completes in ≤ 4 LLM round-trips.
- Works on **all three** providers (subject to `supports_tools`).
- Handshake to `ready` is still < 400 ms.
- stdout remains valid NDJSON for the whole turn, including on error paths.

**Effort:** 2 days.

---

## Phase 7 — Shell tool and interactive permissions

**Goal:** the agent can run commands, and the user is in control of what runs.

`docs/tools.md` is emphatic on the ordering, and it is right:

> Do not add a general-purpose `run_command` tool before validation and explicit
> permissions work.

**Deliverables**

| Module | Contents |
|---|---|
| `app/agent/tools/bash.py` | `BashTool` with per-command allow/deny |
| `app/agent/middleware/permissions.py` | Interactive approval prompt |
| `app/agent/api/sessions.py` | Persisted allow-list |

**Requirements**

- **Per-command permission keys**, not per-tool: `bash(git status)` must be allowlistable
  without allowing `bash(*)`. Parse the command, key on the first token plus a pattern.
- **`is_read_only` varies by argument.** `bash(ls)` is read-only; `bash(rm -rf)` is not.
  This is exactly why `Tool.is_read_only()` takes `args`.
- **Hard timeout, always.** Plus process-group kill on timeout, or orphans accumulate.
- **Deny-list wins in every mode**, including bypass.
- Approval offers: once / always-this-session / always-persist / deny.
- **A denial returns a `ToolMessage`, not an exception.** The model must see "you may
  not do that" and recover, not have the turn die.

**Gate**

- No shell command runs without an explicit decision in `default` mode.
- Deny-list entries are unbypassable.
- A 10-second timeout leaves no orphan processes.
- Plan mode: `bash` is not offered.

**Effort:** 1.5 days.

---

## Phase 8 — Sessions and context management

**Goal:** long conversations stay affordable and resumable.

**Deliverables**

| Module | Contents |
|---|---|
| `app/agent/api/sessions.py` | JSONL append-only session log, `resume()` |
| `app/agent/middleware/context.py` | Compaction at a token threshold |

**Requirements**

- **JSONL, append-only**, one message per line — crash-safe and cheap to tail.
- Use `perf.new_id()` (uuid7) for message ids: time-ordered, so the log sorts naturally
  and any future DB index appends rather than scattering.
- **Compact on a token threshold, not every turn.** Summarising every turn is an extra
  LLM call per turn.
- **Drop stale tool results first.** A 20 KB `grep` output from six turns ago is re-sent
  on every subsequent request — that is compounding latency and cost. Keep the last N
  tool results verbatim, summarise the rest.
- A `resume` request type carrying a session id, rehydrating message history.

**Gate**

- A 50-turn session stays under the model's context limit without truncation errors.
- Resume reproduces state exactly.
- Token count per turn is flat, not monotonically rising.

**Effort:** 1.5 days.

---

## Phase 9 — Deferred

Explicitly **not** in the initial build, per `docs/tools.md`:

| Item | Why deferred |
|---|---|
| Subagents / `Task` tool | Needs the single-agent loop proven first. See [07-multi-agent.md](07-multi-agent.md) — most "multi-agent" needs are one agent with more tools |
| Hooks | Needs a stable tool lifecycle to hook into |
| MCP client | Needs the registry seam (`ToolRegistry.load_from_directory()`) |
| Plugins | Same |
| Text-protocol tool calling | Only needed once a non-tool-calling Ollama model must be supported. See [11-tool-engine.md](11-tool-engine.md) §7 |
| Web tools | High latency, agentic branch only |

---

## Summary

| Phase | Goal | Effort | Gate |
|---|---|---|---|
| **0** | Runtime skeleton | 0.5 d | `--help` < 150 ms, no langgraph imported |
| **1** | Tool contract + `read_file` | 1 d | full loop works, extras rejected |
| **2** | `glob` + `grep` | 1 d | budgets met, `.venv` pruned, loop never blocks |
| **3** | Tool engine | 2 d | **≥90% recovery, 0 false repairs** |
| **4** | `edit_file` + `write_file` | 1 d | no path to a stale edit |
| **5** | Providers | 1 d | 0 ms construction after first call |
| **6** | Agent loop | 2 d | **fixes a real bug in ≤4 round-trips** |
| **7** | Shell + permissions | 1.5 d | nothing runs unapproved |
| **8** | Sessions + context | 1.5 d | flat tokens/turn over 50 turns |

**Total: ~11.5 days to an agent backend that fixes bugs on three providers.**

---

## How to measure

Instrument **stages**, not just totals:

```
sera_tool_duration_seconds{tool, outcome}
sera_tool_repairs_total{tool, repair_kind}
sera_agent_turn_seconds{provider, phase}   # phase: import|graph|model|tools
sera_agent_roundtrips{provider}
```

Three rules that keep the numbers honest:

1. **Benchmark against a stub provider** that returns a fixed tool-call script
   instantly. It is the only way to see SERA's own overhead — benchmarking against a
   real LLM measures the LLM.
2. **Report p95 and p99, never the mean.** Means hide the thread-pool saturation and
   cold-cache cases that are exactly what you are hunting.
3. **Re-run `scripts/bench_runtime.py` after every dependency bump.** Every performance
   claim in these docs is a measurement on one machine at one point in time, not a law.

---

## Decisions to lock before Phase 3

| # | Question | Options | Recommendation |
|---|---|---|---|
| 1 | Fuzzy tool-name cutoff | 0.75 / 0.85 / exact only | **0.85.** At 0.75, `gerp`→`grep` resolves, but so do genuine mistakes. Prefer "unknown tool, here is the list" over running the wrong tool |
| 2 | Repair transparency | silent / logged / shown to user | **Logged + metered.** Surface in `--verbose`. A rising repair rate is the earliest signal a provider has regressed |
| 3 | Max parallel tool calls | 4 / 8 / unbounded | **8.** Bounded by semaphore; unbounded lets one turn saturate the thread pool |
| 4 | Circuit breaker threshold | 2 / 3 / 5 | **3** consecutive, 30 s cooldown |
| 5 | Default permission mode | default / accept_edits | **default.** `accept_edits` is a per-invocation flag, never the default |
| 6 | Tool result truncation | 4 KB / 16 KB / none | **16 KB with a continuation hint.** Tool output is re-sent every turn |
| 7 | Session storage | JSONL / SQLite | **JSONL.** Append-only, crash-safe, greppable. SQLite only if search becomes a real need |
| 8 | Backend process model | persistent sidecar / spawn per invocation | **Persistent sidecar.** Ink spawns Python once and keeps it alive, so the ~1800 ms langgraph import is paid once behind the splash rather than on every command |

---

← [Previous](08-providers.md) · [Index](README.md) · [Next](11-tool-engine.md) →
