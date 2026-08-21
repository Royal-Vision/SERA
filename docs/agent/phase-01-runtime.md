# Phase 01 — Runtime & Protocol

**Effort:** 0.5 day · **Depends on:** [00](phase-00-architecture.md)

---

## 1. Why this phase exists

This phase looks trivial and is not. It sets one thing that is nearly impossible to
retrofit: **the import boundary.**

By the time twenty modules exist, one of them imports LangGraph at module scope, and the
1.8 s is baked in permanently — finding it later means auditing every import chain in the
codebase. Set it now, enforce it with a test, and it stays free.

Measured on this machine, CPython 3.14.7:

```
import langgraph.graph   →  1798 ms
import langchain.agents  →   235 ms
import langchain_ollama  →   251 ms
```

The second reason: the Ink frontend needs a protocol to talk to, and protocol changes
are cross-repo changes. Getting the frame vocabulary roughly right on day one saves a
painful synchronisation later.

---

## 2. The architecture decision

### Transport: NDJSON over stdio

| Option | Verdict |
|---|---|
| **NDJSON over stdio** | **Chosen.** No port, no auth, no CORS, no TLS handshake; parent owns process lifetime; trivially debuggable with `tee` |
| Local HTTP + SSE | Needs a port (collision, firewall prompts), auth to stop other local processes, and CORS if a browser ever attaches |
| WebSocket | All of HTTP's costs plus a handshake, for duplex we barely need |
| gRPC | Codegen and a schema compiler for a two-process link on one machine |

One JSON object per line, both directions. The parent (Ink) spawns the child (Python)
and owns its lifetime — no orphan processes, no stale ports.

### Process model: persistent sidecar

Ink spawns the Python process **once** and keeps it alive. The ~1800 ms import is paid
once, behind the Ink splash, rather than on every command.

The alternative — spawn per command — makes the lazy-import rules load-bearing for
correctness rather than just hygiene, and caps you at ~2 s minimum per invocation. Only
choose it if the frontend genuinely cannot hold a child process.

**Either way, keep the discipline.** It costs nothing and preserves the option.

---

## 3. The protocol

```
→ {"id":"01J…","type":"prompt","text":"fix the bug in calc.py","cwd":"/proj","mode":"default"}
← {"id":"01J…","type":"ready"}
← {"id":"01J…","type":"token","text":"Looking"}
← {"id":"01J…","type":"tool_start","tool":"read_file","args":{"path":"src/calc.py"}}
← {"id":"01J…","type":"tool_end","tool":"read_file","ok":true,"ms":3.1,"repairs":[]}
← {"id":"01J…","type":"permission_request","tool":"bash","key":"bash(rm -rf build)","risk":"high"}
→ {"id":"01J…","type":"permission_response","decision":"allow_once"}
← {"id":"01J…","type":"done","turns":3,"ms":4120}
← {"id":"01J…","type":"error","message":"provider unreachable","recoverable":true}
```

**Frame types (client → server):** `prompt`, `permission_response`, `cancel`, `doctor`,
`resume`

**Frame types (server → client):** `ready`, `token`, `tool_start`, `tool_end`,
`permission_request`, `done`, `error`

Design rules:

- **Every frame carries `id`**, correlating to the originating request. Cancellation and
  concurrent turns both need it.
- **`tool_start` fires before execution**, not after. It is the cheapest perceived-latency
  win available: Ink renders "reading src/calc.py…" while it happens.
- **`repairs` is exposed on `tool_end`.** Surfacing it in a verbose mode is how you
  notice a provider has regressed.
- **Additive evolution only.** Unknown frame types must be ignored by the client, not
  fatal. That lets the two repos ship independently.

### The rule that breaks everything if violated

**stdout carries protocol frames only.** Every log line, traceback, warning and
diagnostic goes to stderr. A single stray `print()` desynchronises the client — and
because JSON parsing fails on the *next* line, the bug appears far from its cause.

Enforce it: in the server entry point, reassign `sys.stdout` to a guarded writer and
point the logging config at stderr before anything else runs.

---

## 4. What to build

```
app/agent/perf.py               runtime switches (stdlib only)
app/agent/server/__main__.py    stdio loop
app/agent/server/protocol.py    frame schemas
scripts/bench_runtime.py        the measurement harness
```

### `perf.py`

```python
def apply_performance_mode() -> dict[str, Any]: ...   # call first, before anything
def configure_stdio() -> None: ...
def install_event_loop_policy() -> str: ...
def enable_eager_tasks(loop=None) -> bool: ...
def freeze_after_warmup() -> int: ...
def tune_gc() -> None: ...

dumps(obj) -> bytes ; loads(data) -> Any             # orjson
compress(b) -> bytes ; decompress(b) -> bytes        # zstd
pack_vector(list[float]) -> bytes ; unpack_vector(bytes) -> list[float]
new_id() -> str                                       # uuid7
```

**`perf.py` imports stdlib only.** No pydantic, no langchain. It runs before the first
frame is written.

### Measured wins — adopt these

All from `scripts/bench_runtime.py` on CPython 3.14.7:

| Change | Before | After | Δ |
|---|---|---|---|
| `orjson.dumps` vs `json.dumps().encode()` | 89.9 ms | 21.0 ms | **+77%** |
| `array('f')` vs JSON floats *(decode)* | 974 ms | 8.2 ms | **+99%** |
| `zstd-1` vs `gzip-6` *(compress)* | 8.4 ms | 1.8 ms | **+79%** |
| `asyncio.eager_task_factory` | 53.9 ms | 28.9 ms | **+46%** |
| `gc.freeze()` after warmup | 9.7 ms | ~0 ms | **+100%** |

**`eager_task_factory` deserves the explanation.** The agent hot path is full of awaits
that complete *without suspending* — permission checks, schema validation, cache hits,
metric emission. Each normally costs a Task allocation plus an event-loop round-trip.
The eager factory runs them inline until they actually block.

**`gc.freeze()` deserves one too.** Call it once, at the end of startup, after the tool
registry, provider clients and compiled graph exist. Those live for the process lifetime;
having the collector rescan them on every pass is pure waste.

### Two honest negatives

Recorded so nobody repeats the experiment:

- **`uuid7` is 73% slower to generate** than `uuid4` (0.6 µs vs 0.45 µs). Adopt it anyway
  — measured index locality is **100% ascending vs 50%**, which is what matters for any
  future B-tree. Not for speed.
- **`InterpreterPoolExecutor` (PEP 734) is unproven here.** The benchmark compared
  `len()` against a regex scan — not a valid comparison. Ignore the number it printed.
  Subinterpreters also cannot host torch or numpy.

### Not available on this build

- **Free-threading** — venv is `cpython-3.14.7-…-none`, GIL enabled. Would need
  `uv python install 3.14t`, and most C extensions are not ready.
- **Tail-call interpreter** — needs a clang-19 build; this is MSVC.

### Windows

`winloop` is installed, so `install_event_loop_policy()` returns `winloop`. And
`configure_stdio()` is not optional: Windows consoles default to cp1252 and raise
`UnicodeEncodeError` on non-Latin-1 bytes. Here that would corrupt the protocol stream,
not merely the display.

---

## 5. Implementation style

**Read frames with a bounded reader.** A malformed or enormous line must not be able to
exhaust memory:

```python
MAX_FRAME = 8 * 1024 * 1024
async for line in reader:          # newline-delimited
    if len(line) > MAX_FRAME: ...  # error frame, do not parse
```

**Never block the loop on stdout.** Writes go through an `asyncio.Queue` drained by a
single writer task, so a slow consumer applies backpressure without deadlocking the
agent.

**Warm the graph import during the handshake.** Kick off `build_agent()` in a background
thread the moment `ready` is sent, so the ~1800 ms overlaps with the user typing their
first prompt rather than blocking it.

---

## 6. Gate

```
cold start → "ready" frame                 < 400 ms
"langgraph" in sys.modules at handshake    False
"torch"     in sys.modules ever            False
stdout contains only valid NDJSON          always
```

Plus a regression test that spawns the process, sends a `doctor` frame, and asserts every
stdout line parses as JSON.

---

← [Previous: Phase 00 — Architecture](phase-00-architecture.md) · [Index](README.md) · [Next: Phase 02 — Tool Contract](phase-02-tool-contract.md) →
