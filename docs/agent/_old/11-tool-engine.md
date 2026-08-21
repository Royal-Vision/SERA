# Tool Engine Architecture

**Part of the [SERA Agent implementation plan](README.md).**

> The goal of this document: **make tool calls fail less, and make the failures that
> remain self-correcting.** A tool call that fails and teaches the model nothing costs
> a full LLM round-trip — the most expensive thing in the system.

---

## 1. Why tool calls fail

Most "tool failures" are not the tool failing. They are the *model* producing something
the schema rejects, and a naive executor turning that into a dead turn.

```mermaid
graph LR
    subgraph MODEL["Model-side failures  (~75%)"]
        F1["Malformed JSON<br/>fences, prose, trailing commas"]
        F2["Wrong types<br/>'5' for int, 'yes' for bool"]
        F3["Hallucinated tool name<br/>readfile / functions.read_file"]
        F4["Wrong enum casing<br/>CONTENT vs content"]
        F5["Truncated output<br/>hit max_tokens mid-call"]
        F6["Wrong tool chosen<br/>too many, vague descriptions"]
    end
    subgraph STATE["State-side failures  (~20%)"]
        F7["Stale precondition<br/>edit before read"]
        F8["Concurrent conflict<br/>two writes, one file"]
        F9["Path escape<br/>../../etc/passwd"]
    end
    subgraph RUNTIME["Runtime failures  (~5%)"]
        F10["Timeout"]
        F11["Genuine tool bug"]
        F12["Retry storm<br/>same bad call, 6 times"]
    end

    style MODEL fill:#1f2937,stroke:#60a5fa,color:#e5e7eb
    style STATE fill:#1f2937,stroke:#fbbf24,color:#e5e7eb
    style RUNTIME fill:#1f2937,stroke:#f87171,color:#e5e7eb
```

The split matters: **three quarters of failures are recoverable without an LLM
round-trip.** That is the entire thesis of this engine.

---

## 2. The pipeline

Each stage either fixes the problem locally or produces an error message written as a
*prompt* — something the model can act on next turn.

```mermaid
flowchart TD
    IN["Tool call from model<br/>{id, name, raw_args}"] --> R1

    R1{"resolve_tool_name"}
    R1 -->|exact| R2
    R1 -->|"case / style / namespace<br/>fuzzy ≥0.85"| R2
    R1 -->|no match| E1["UNKNOWN_TOOL<br/>+ list of real tools"]

    R2{"circuit open?"}
    R2 -->|"3 consecutive fails"| E2["CIRCUIT_OPEN<br/>'try another approach'"]
    R2 -->|no| R3

    R3["repair_json<br/>fences · prose · commas<br/>single-quotes · truncation"]
    R3 -->|unparseable| E3["INVALID_ARGS"]
    R3 --> R4

    R4["coerce_to_schema<br/>'5'→5 · 'yes'→true<br/>'CONTENT'→'content'"]
    R4 --> R5

    R5{"pydantic validate<br/>extra='forbid'"}
    R5 -->|fail| E4["INVALID_ARGS<br/>+ constraints + valid params"]
    R5 --> R6

    R6{"PermissionPolicy.check"}
    R6 -->|DENY| E5["DENIED + reason"]
    R6 -->|ASK| E6["NEEDS_APPROVAL<br/>→ interrupt to user"]
    R6 -->|ALLOW| R7

    R7["execute under<br/>asyncio.timeout(budget)"]
    R7 -->|TimeoutError| E7["TIMEOUT"]
    R7 -->|Exception| E8["ERROR<br/>traceback→log, short msg→model"]
    R7 --> OK["OK + ToolResult"]

    OK --> M["record circuit success<br/>emit duration vs budget_ms"]
    E1 & E2 & E3 & E4 & E5 & E6 & E7 & E8 --> M2["record failure<br/>RepairLog → metrics"]
```

**No path escapes as an exception.** Every terminal state is a `ToolResult` the model
reads. An exception reaching the agent loop kills the turn and loses all in-flight work
— the worst possible outcome.

---

## 3. Errors are prompts

This is the highest-leverage idea in the document, and it costs nothing to implement.

| Naive error | What the model does next | Engine error | What the model does next |
|---|---|---|---|
| `ValidationError` | repeats the same call | `top_n: Input should be ≤ 10 — expected type=integer, maximum=10` | sends `10` |
| `KeyError: 'pat'` | guesses another name | `extra_forbidden — valid parameters are: pattern, path, glob, limit` | sends `pattern` |
| `Tool not found` | invents another name | `Unknown tool 'gerp'. Available: edit_file, glob, grep, read_file, write_file` | sends `grep` |
| `Permission denied` | retries identically | `write_file needs approval (default). Ask the user, or use plan mode.` | asks first |

Implemented in `_actionable_validation_error()` in
[executor.py](../../app/agent/engine/executor.py) — it appends the offending field's
JSON-Schema constraints to Pydantic's message.

---

## 4. Parallel dispatch and conflict detection

Running independent tools concurrently is the biggest latency win available inside a
single turn. But naive parallelism introduces correctness bugs.

```mermaid
flowchart TD
    A["Batch of N tool calls"] --> B{"for each call"}
    B --> C{"spec.concurrency_safe?"}
    C -->|"no<br/>(bash, write_file)"| D["seal current batch<br/>run this one ALONE"]
    C -->|yes| E{"path ∩ claimed paths?"}
    E -->|"yes — conflict<br/>read+write same file"| F["seal current batch<br/>start a new one"]
    E -->|no| G["add to current batch<br/>claim its paths"]
    D --> H
    F --> H
    G --> H["next call"]
    H --> B
    B -->|done| I["execute batches in order<br/>within a batch: asyncio.gather"]
    I --> J["reassemble in REQUESTED order"]
```

Two rules, both in `_plan_batches()`:

1. **Not `concurrency_safe` ⇒ runs alone.** `bash` and `write_file` never share a batch.
2. **Overlapping paths ⇒ separate batches**, even when both tools are individually safe.
   A read racing a write on one file is a genuine correctness bug.

**Ordering guarantee:** results are reassembled in the order requested, regardless of
completion order. Models reason about tool results positionally — shuffling them causes
confusing downstream errors.

```mermaid
gantt
    title Serial vs batched dispatch — 5 calls
    dateFormat X
    axisFormat %s
    section Serial
    read_file a      :0, 25
    read_file b      :25, 50
    grep             :50, 300
    glob             :300, 420
    write_file       :420, 480
    section Batched
    "read a ∥ read b ∥ grep ∥ glob"  :0, 300
    write_file (alone, conflicts)     :300, 360
```

Same work, 480 ms → 360 ms, with the write still correctly serialized.

---

## 5. Preconditions as a state machine

The most damaging tool failure is not an error — it is a *successful* edit against a
stale view of the file. The engine tracks per-file state within a turn.

```mermaid
stateDiagram-v2
    [*] --> Unknown: file never touched
    Unknown --> Read: read_file succeeds<br/>(record mtime + hash)
    Read --> Edited: edit_file<br/>(verify hash unchanged)
    Edited --> Read: re-read
    Unknown --> Rejected: edit_file attempted
    Read --> Rejected: hash changed on disk
    Rejected --> Read: forced re-read
    Edited --> [*]
    note right of Rejected
        "You must read the file before
        editing it" — or —
        "File changed on disk since you
        read it; re-read before editing."
        Both are actionable prompts.
    end note
```

Enforcing read-before-edit removes an entire class of silent data loss, and the hash
check catches the case where an external process (or a parallel batch) modified the file
mid-turn.

---

## 6. Layered architecture

```mermaid
graph TD
    subgraph L6["CLI  ·  entry point"]
        CLI["sera chat<br/>lazy-imports everything below"]
    end
    subgraph L5["Graph  ·  LangGraph"]
        GRAPH["StateGraph: classify → act → stream<br/>create_agent only for the agentic branch"]
    end
    subgraph L4["Engine  ·  app/agent/engine"]
        ENG["ToolEngine<br/>resolve · repair · validate · authorize · dispatch"]
        REP["repair.py<br/>JSON recovery · coercion · fuzzy names"]
        CIRC["circuit breaker<br/>per tool"]
    end
    subgraph L3["Policy  ·  app/agent/base.py"]
        POL["PermissionPolicy<br/>pure, synchronous, no I/O"]
    end
    subgraph L2["Tools  ·  app/agent/tools"]
        T1["read_file"]; T2["glob"]; T3["grep"]; T4["edit_file"]; T5["write_file"]
    end
    subgraph L1["Runtime  ·  app/agent/perf.py"]
        PERF["orjson · zstd · uuid7<br/>eager tasks · gc.freeze"]
    end

    CLI --> GRAPH --> ENG
    ENG --> REP
    ENG --> CIRC
    ENG --> POL
    ENG --> T1 & T2 & T3 & T4 & T5
    T1 & T2 & T3 & T4 & T5 -.-> PERF
    ENG -.-> PERF
```

**The dependency rule:** arrows point downward only. Tools never import the engine, the
engine never imports the graph, and nothing below L5 imports LangGraph. That last one is
not stylistic — see §8.

---

## 7. Reference architectures

Two patterns worth borrowing. I am confident about the first; **treat the second as a
sketch to verify** rather than a specification — say the word and I will look up the
current design before you build against it.

### Hermes-style text-protocol tool calling

Nous Research's Hermes models call tools through a *text* protocol rather than a
provider-native API: schemas are injected into the system prompt, and the model emits
delimited blocks that the harness parses out of the completion stream.

```mermaid
flowchart LR
    A["system prompt<br/>+ tool schemas as JSON"] --> B["model completion"]
    B --> C{"scan for<br/>delimited block"}
    C -->|found| D["parse JSON inside<br/>→ repair.py"]
    C -->|none| E["plain text answer"]
    D --> F["execute"] --> G["append result block"] --> B
```

**Why this matters for SERA specifically:** many Ollama models have no native
tool-calling endpoint. A text protocol is the *universal fallback* — it works on any
model that can follow a format. This is what lets "sign in with Ollama" work with models
that Codex-style native function calling would exclude.

The cost is parsing fragility, which is exactly what
[repair.py](../../app/agent/engine/repair.py) already absorbs. Recommendation:
**native tool calling when `supports_tools`, text protocol as the fallback**, with the
same repair layer behind both.

### Gateway / skills separation

The pattern (as seen in assistant frameworks such as OpenClaw and others in that family)
separates a long-lived **gateway** process from swappable **skill** modules, so
capabilities can be added without restarting or redeploying the core.

Worth borrowing for SERA: the tool registry is already the right seam for this — a
`load_from_directory()` on `ToolRegistry` would give plugin-style tools. Worth deferring
until the core loop is proven, exactly as `docs/tools.md`'s build order says.

---

## 8. Making it fast in Python

Measured on this machine, CPython 3.14.7 — full results in
[bench_runtime.py](../../scripts/bench_runtime.py).

### The finding that dominates a CLI

```
import langgraph.graph   →   1798 ms
import langchain.agents  →    235 ms
import langchain_ollama  →    251 ms
```

**A CLI that pays 1.8 s before printing anything is dead on arrival.** So:

```mermaid
flowchart TD
    START["sera <command>"] --> P["apply_performance_mode()<br/>~2 ms — stdlib only"]
    P --> D{"command?"}
    D -->|"--help / --version"| FAST["print and exit<br/>TOTAL ~50 ms"]
    D -->|"config / providers"| MED["+ contracts, registry<br/>TOTAL ~120 ms"]
    D -->|"chat / run"| SLOW["import langgraph HERE<br/>behind a spinner<br/>TOTAL ~2 s"]

    style FAST fill:#064e3b,stroke:#34d399,color:#d1fae5
    style MED fill:#064e3b,stroke:#34d399,color:#d1fae5
    style SLOW fill:#7c2d12,stroke:#fb923c,color:#ffedd5
```

Rules that follow:
- `perf.py`, `contracts.py`, `base.py` and every tool module import **stdlib + pydantic only**.
- LangGraph is imported inside the function that builds the graph, never at module scope.
- The graph is compiled **once** and cached; compiling per invocation is pure waste.
- Warm the import in a background thread the moment the user starts typing their prompt.

### Measured runtime wins

| Optimization | Before | After | Δ | Where |
|---|---:|---:|---:|---|
| `orjson.dumps` vs `json.dumps` | 89.9 ms | 21.0 ms | **+77%** | `perf.dumps` |
| `array('f')` vs JSON floats *(decode)* | 974 ms | 8.2 ms | **+99%** | `perf.unpack_vector` |
| `zstd-1` vs `gzip-6` *(compress)* | 8.4 ms | 1.8 ms | **+79%** | `perf.compress` |
| `eager_task_factory` | 53.9 ms | 28.9 ms | **+46%** | `perf.enable_eager_tasks` |
| `gc.freeze()` after warmup | 9.7 ms | ~0 ms | **+100%** | `perf.freeze_after_warmup` |
| `uuid7` index locality | 50% ordered | 100% ordered | — | `perf.new_id` |

Two honest negatives, so nobody repeats the experiment:

- **`uuid7` is 73% slower to generate** than `uuid4` (0.6 µs vs 0.45 µs). Irrelevant at
  our call volume — adopt it for the B-tree locality, not for speed.
- **`InterpreterPoolExecutor` (PEP 734) is unproven here.** My benchmark compared
  `len()` against a regex scan, which is not a valid comparison, and the number it
  printed should be ignored. Subinterpreters also cannot host torch or numpy. Revisit
  only for sustained pure-Python CPU work in a long-lived pool.
- **Free-threading is not available** in this venv (`cpython-3.14.7-...-none`, GIL
  enabled), and the tail-call interpreter needs a clang-19 build — this is MSVC.

### Concurrency model

```mermaid
flowchart LR
    subgraph LOOP["event loop — never blocks"]
        E["ToolEngine"] --> S["asyncio.Semaphore(8)"]
    end
    subgraph IO["async I/O — anyio"]
        R["read_file"]; W["write_file"]
    end
    subgraph CPU["worker threads — anyio.to_thread"]
        G["glob: os.walk"]; GP["grep: regex scan"]; RG["ripgrep subprocess"]
    end
    S --> R & W
    S --> G & GP & RG
```

Anything that blocks — `os.walk`, a regex scan over a repo, a subprocess — goes to a
thread. One blocking `glob` on the event loop freezes every other concurrent tool call,
which under load does not degrade gracefully; it collapses.

---

## 9. What to build next

| # | Item | Why |
|---|---|---|
| 1 | `preconditions.py` — the §5 state machine | removes silent stale-edit data loss |
| 2 | `edit_file` / `write_file` tools | completes `docs/tools.md` build step 3 |
| 3 | `graph/adapter.py` — `Tool` → LangChain `BaseTool` | keeps LangGraph out of the tool modules |
| 4 | Text-protocol fallback parser | makes non-tool-calling Ollama models work |
| 5 | `bash` tool + allow/deny rules | `docs/tools.md` build step 4 — **after** 1–3 |
| 6 | Golden-set repair tests | lock in the recovery rates before they regress |

---

← [Previous](09-phases.md) · [Index](README.md)
