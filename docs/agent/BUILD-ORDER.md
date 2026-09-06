# Build Order — the file-by-file path

**Companion to [README.md](README.md).** The phase docs answer *why* and *what*.
This answers ***what do I type first*** — the order to create files in, and the
one check that tells you a step is done.

The phase docs are ordered for **shipping**. This is ordered for **learning**, and it
differs in three deliberate places, flagged as **DEVIATION** below.

---

## 0. Where you are right now

`app/agent/` is empty except `_old/`, which holds the previous implementation
(2,952 lines). It is reference, not the build — the README already said to treat it
that way.

**How to use `_old/`:** write your version first, *then* diff. Reading it first means
you copy it; writing first means you find out where your design differs, and why.
Every file is recoverable with `git mv app/agent/_old/<file>.py app/agent/`.

**One file worth copying verbatim rather than retyping:** `_old/perf.py`. It is runtime
plumbing — event-loop policy, gc tuning, orjson wrappers — with zero agent concepts in
it. There is nothing to learn from typing it again. Copy it at Step 10, not before.

---

## 1. Casbin — the answer

**Not for tool permissions. Yes, later, for API authorization.** These are two different
questions that happen to share the word "permission."

### Why not for tool calls

| What the agent's gate needs | What Casbin gives |
|---|---|
| Three outcomes: `ALLOW` / `DENY` / **`ASK`** | `enforce()` returns a **bool** |
| Rules keyed on *runtime facts* — `read_only`, `risk`, `mode` | subject / object / action tuples |
| Policy that **mutates mid-turn** (user approves `bash(git *)`, the set grows) | policy loaded from a store |
| Ordered precedence, where **the order is the security property** | `policy_effect` — deny-override, but not an ordered cascade |
| A pure function you can table-test with no mocking | a matcher string evaluated by `simpleeval` |

You *can* force it: push `mode`, `risk` and `read_only` into the request tuple as ABAC
attributes, and treat "no match" as `ASK`. It works. It also turns 40 lines of readable
Python into a matcher expression you cannot set a breakpoint inside — and Phase 11's
gate is *"full decision matrix covered by table tests."* You would be paying a
dependency to make your most security-critical code harder to test.

**The decisive fact: there is no subject.** Casbin answers "may *this user* do X."
Grep the repo — there is no `User` model, no roles, no auth anywhere. The agent's gate
asks "may *this call* run, given this session's posture." Different question.

### Where Casbin does belong

Two layers, and they compose cleanly:

```
HTTP request  →  [ Casbin ]   which USER / ROLE may reach this route,
                              see this tenant's data, use which tools
                                    │
                                    ▼
                          ToolRegistry.for_role(role)   ← Casbin picks the tool subset
                                    │
                                    ▼
agent turn    →  [ PermissionPolicy ]   may THIS CALL run — allow / deny / ask
                                    │
                                    ▼
                                 execute
```

Casbin decides **which tools exist for you at all**. `PermissionPolicy` decides
**whether this specific invocation runs right now**. Casbin never sees a tool call; the
policy never sees a user.

For a medical product with tenants and clinician/admin roles, that upper layer is real
work and Casbin fits it well — RBAC with domains (`g(r.sub, p.sub, r.dom)`) is exactly
multi-tenancy, and there is an async SQLAlchemy adapter that suits your asyncpg stack.

**Do it when you have users.** That is after Step 11, not now. Adding it today means
writing a policy engine with no subjects to enforce against.

---

## 2. The build order

Each step gives **the file**, **what it must do**, and **the gate** — the check that
tells you to move on. Do not skip a gate. The gates are the point.

---

### Step 0 · Make the tests runnable · ~15 min

`pyproject.toml` has `testpaths = ["app/blueprints/rag_eval/tests"]`, which points away
from everything you are about to build.

- create `tests/agent/__init__.py`
- add `"tests"` to `testpaths`

**Gate:** `.venv/Scripts/python.exe -m pytest tests/ -q` runs and collects 0 tests
without erroring.

> Use `.venv/Scripts/python.exe` explicitly, always. `python` on PATH is 3.12.9; the
> venv is 3.14.7. Mixing them will cost you an afternoon.

---

### Step 1 · `app/agent/contracts.py` · Phase 02 · ~2 h

**The vocabulary.** Nothing runs yet; everything later depends on it. This is the most
load-bearing file in the system, which is why it is first.

Build, in this order:

1. `ToolResult` — `content`, `is_error`, `metadata`, `truncated`, plus `.ok()` / `.error()`
2. `RiskLevel`, `PermissionMode`, `Decision` — `StrEnum`
3. `PermissionResult`, `PermissionContext` — data only, no logic
4. `ToolSpec` — `name`, `category`, `risk`, `read_only`, `concurrency_safe`,
   `timeout_s`, `budget_ms`, `cache_ttl_s`, `plan_mode_safe`
5. `AgentContext` — `cwd`, `permission`, deadlines, `resolve_in_project()`

**Two things to get right, because retrofitting them is painful:**

- **`ToolSpec.__post_init__` must reject incoherent specs.** A `read_only` tool that is
  not `SAFE`; a cache TTL on a mutating tool; a mutating tool marked `plan_mode_safe`.
  Catching these at import time means an impossible tool can never reach production.
- **`resolve_in_project()` is the single chokepoint for path traversal.** Call
  `.resolve()` *first*, so a symlink cannot step outside, then check containment. Every
  filesystem tool routes through it. A second path-resolution code path later means two
  places to get it wrong.

**Import rule, set here or never:** stdlib and pydantic only. No langchain, no
langgraph, no torch. `import langgraph.graph` costs ~1800 ms.

**Gate** — `tests/agent/test_contracts.py`:
- `resolve_in_project("../../../etc/passwd")` raises `ValueError`
- `resolve_in_project("src/x.py")` returns a path under `cwd`
- a symlink pointing outside the project is rejected
- `ToolSpec(read_only=True, risk=HIGH, ...)` raises
- `ToolSpec(read_only=False, plan_mode_safe=True, ...)` raises

---

### Step 2 · `app/agent/base.py` · Phase 02 · ~2 h

**The `Tool` ABC and the registry.** Write `PermissionPolicy` as a **stub that always
returns `ALLOW`** — the real one is Step 8, once you have tools whose decisions differ.

- `Tool(ABC, Generic[InputT])` — `spec`, `input_model`, `json_schema()`, abstract
  `async call()`
- `is_read_only(args)`, `risk_for(args)`, `permission_key(args)` — **all take `args`.**
  They look redundant now. They are the entire reason `bash(ls)` can auto-allow while
  `bash(rm -rf)` prompts. Do not simplify them into properties.
- `run(raw, ctx)` — validate → timeout → execute, and **never raises**
- `ToolRegistry` — name → tool, duplicate-name rejection, `for_mode()`

**The rule that shapes this file:** an exception escaping `run()` kills the turn, and the
user loses all in-flight work. Catch everything, log the traceback to *stderr*, return a
short message to the model.

**Gate** — `tests/agent/test_base.py`:
- `run()` with invalid args returns `is_error=True` and does not raise
- a tool whose `call()` raises `ZeroDivisionError` returns `is_error=True`
- a tool that sleeps past `timeout_s` returns a timeout result
- registering two tools under one name raises

---

### Step 3 · `app/agent/tools/read.py` · Phase 03 · ~1.5 h

**Your first real tool.** `read_file` is chosen because it is the smallest possible
security surface on which to prove the whole pipeline.

Decisions to make deliberately — every one is a token-cost decision:

- **line numbers in output?** Yes — `edit_file` and error messages both need them.
- **max bytes, and what you say when you truncate.** The model must *know* the view is
  partial, or it will confidently reason about a file it only half saw.
- **offset / limit parameters?** Yes — a 10k-line file must not be all-or-nothing.
- **binary files** — detect and refuse. A PNG in the context window is pure waste.
- **encoding failures** — `errors="replace"`, never crash.

**Gate:** read a normal file, a missing file, a 5 MB file, a `.png`, and
`../../../etc/passwd`. Five calls, five `ToolResult`s, zero exceptions.

---

### Step 4 · `scripts/loop.py` — the 40-line agent · **DEVIATION** · ~2 h

> **Not in the phase docs. Do it anyway.** It is the highest-value two hours in this plan.

The docs go from tools (04) straight to LangGraph (08). Do that, and LangGraph feels like
magic — and you will not be able to debug it, because you never saw the loop it replaces.

So write the loop by hand, once, in a throwaway script. No LangGraph, no engine, no
graph. Direct `ChatOllama`:

```
messages = [system, user]
loop:
    response = llm.invoke(messages, tools=registry.schemas())
    if not response.tool_calls:
        print(response.content); break
    messages.append(response)
    for call in response.tool_calls:
        result = await registry.get(call.name).run(call.args, ctx)
        messages.append(ToolMessage(result.content, call.id))
```

That is the entire concept of an agent. Everything in Phases 05–09 is hardening this
loop — nothing more.

**Gate, and the moment the project becomes real:** point it at a local Ollama model, ask
*"what does main.py do?"*, and watch it call `read_file` and then answer from what it read.

**Then deliberately break it.** This is where the next four phases come from:

| Break it this way | The phase that fixes it |
|---|---|
| Ask for a file that does not exist | 05 — errors that teach the model |
| Use a small model until it emits `{'path': 'x.py',}` — single quotes, trailing comma | 05 — `repair.py` |
| Ask something needing 6 files, and time it | 05 — parallel batching |
| Ask it to edit a file it never read | 06 — preconditions |

Keep this script forever. It is the fastest way to answer "is this my agent, or my
model?" for the rest of the project.

---

### Step 5 · `tools/glob.py` + `tools/grep.py` · Phase 04 · ~1 d

The biggest single determinant of **how many turns a task takes** — so the biggest
determinant of latency and cost, more than any model choice.

- `glob` — find by name. Respect `.gitignore`. Sort by mtime; recently-touched files are
  usually the relevant ones.
- `grep` — content search. Shell out to `rg` when present, fall back to Python. Cap
  matches. Support `-A`/`-B` context and a files-only mode.

**Gate:** on this repo, `grep("PermissionPolicy")` returns hits in under 300 ms and the
output is under 2 KB. Both numbers matter — the first is latency, the second is context
you re-send on every subsequent turn.

---

### Step 6 · `engine/repair.py` → `engine/executor.py` · Phase 05 · ~2 d

**The phase that differentiates the product.** ~75% of tool failures are recoverable
without an LLM round-trip, and each round-trip saved is a full model latency.

`repair.py` first — it is pure functions, easy to test:

- `repair_json` — single quotes, trailing commas, markdown fences, doubled braces
- `resolve_tool_name` — fuzzy match `read_files` → `read_file`
- `coerce_to_schema` — `"5"` → `5`, `"true"` → `True`, scalar → `[scalar]`
- `RepairLog` — record every repair. Surfacing it is how you notice a provider regressed.

Then `executor.py`: resolve → repair → coerce → validate → preconditions → permission →
batch → execute → shape.

Plus two things the hand loop had no answer for:

- **circuit breaker** — after N consecutive failures a tool short-circuits with a message
  telling the model to *stop trying*. Without it, one broken tool eats the whole step budget.
- **conflict-aware batching** — parallelise, but never put a read and a write to the same
  path in one batch. That is a correctness bug, not a performance question.

**The rule for every error string in this file: an error message is a prompt.**
`"ValidationError"` teaches the model nothing and it repeats the mistake.
`"top_n must be <= 10, you sent 50"` gets it right on the retry.

**Gate:** feed the executor 20 malformed calls a small model actually produced; at least
15 succeed without reaching the LLM again.

---

### Step 7 · `engine/preconditions.py` + `tools/edit.py` + `tools/write.py` · Phase 06 · ~1 d

**Preconditions first, then the mutating tools** — the state machine is what makes those
tools safe to write.

The failure this prevents is not an error. It is a *successful* edit applied to a file
the agent last saw three turns ago, silently discarding whatever changed in between. An
error is recoverable; silent data loss is not.

Two invariants: **read-before-edit**, and **unchanged-since-read** (size and mtime first,
sha256 only when that is ambiguous).

**Gate:** read a file → modify it externally → try to edit → refused, with a message
saying exactly what to do. And `edit_file` on a never-read file → refused.

---

### Step 8 · `PermissionPolicy`, for real · Phase 11a · **DEVIATION** · ~0.5 d

> **DEVIATION: split Phase 11 in two.** Write the *policy* now — the executor is already
> calling `policy.check()`, and you have been stubbing it since Step 2. Defer only the
> `bash` tool to the end, for exactly the reason the phase doc gives: shipping the
> permission model on a tool that can `rm -rf` is the wrong place to discover a bug in it.

Replace the Step 2 stub with the real cascade. **The order is the security property:**

1. `always_deny` — wins in **every** mode, including `BYPASS`. A deny-list that bypass
   can override is not a deny-list, it is a suggestion.
2. plan mode and mutating → `DENY`. Checked **before** allow-lists: "show me, don't
   touch" cannot be overridden by an approval granted earlier.
3. `read_only` → `ALLOW`
4. `BYPASS` → `ALLOW`
5. allow-lists, persisted then session → `ALLOW`
6. `accept_edits` and not `HIGH` → `ALLOW`
7. otherwise → `ASK`

Keep `check()` **pure and synchronous** — no prompting, no I/O, no await. The caller
turns `ASK` into a terminal prompt, a LangGraph `interrupt`, or an automatic denial.
That purity is exactly what makes the gate below a table test with no mocking.

**Gate:** every `mode × risk × read_only` combination as a parametrised table test.
Specifically: `always_deny` holds in all four modes; plan mode beats a matching
allow-list entry; `bash(git *)` does **not** grant `bash(rm)`.

---

### Step 9 · `providers/base.py` · Phase 07 · ~1 d

Ollama and OpenAI-compatible providers behind one registry.

**The one thing that matters here: `@lru_cache` the model instances.** Constructing a
chat model per request means a new httpx client, a new connection pool and a new TLS
handshake — 50–300 ms before the model is asked anything. That alone blows the budget,
and it is the top defect in the current `blueprints/agent/routes.py`.

Second: pass `keep_alive` to Ollama, or it evicts the model after ~5 minutes and the next
request pays a multi-second reload.

**Gate:** `get_model("ollama")` twice returns the *same object*. A second call to a warm
provider costs under 10 ms of SERA overhead.

---

### Step 10 · `graph/agent.py` · Phases 08–09 · ~2 d

**Now** replace the hand loop with LangGraph — and because you wrote Step 4, you will
know exactly what it is doing and what it buys you.

Hand-build the `StateGraph`; do **not** use `create_agent`. Its built-in `ToolNode`
bypasses your entire engine — repair, permissions, circuit breaker, batching. The loop
itself is ~15 lines; all the value is in what the tools node delegates to.

Import LangGraph **inside** `build_agent()`, never at module scope.

Copy `_old/perf.py` back now — the entry point needs it.

**Gate:** `sera run "add a docstring to X"` works end to end, and `sera --help` still
returns in under ~100 ms. That second number is your import discipline still holding.

---

### Step 11 · Server, sessions, guardrails · Phases 01, 10, 12 · ~3 d

NDJSON over stdio (`server/__main__.py`, `protocol.py`), session persistence and
compaction, PII guardrails.

**The rule that breaks everything if violated:** stdout carries protocol frames only.
Every log line, warning and traceback goes to stderr. One stray `print()` desynchronises
the client — and because JSON parsing then fails on the *next* line, the bug appears far
from its cause. Reassign `sys.stdout` to a guarded writer in the entry point before
anything else runs.

---

### Step 12 · `tools/bash.py` · Phase 11b · ~0.5 d

Last, deliberately. Everything before this was safe *by structure* — confined to the
project, read-before-edit, no network. `bash` cannot be made safe by structure, so it
needs your permission gate to be correct first.

- `permission_key` → `f"bash({args.command})"`. This is why `permission_key` took `args`
  back in Step 2.
- `is_read_only` varies by verb — `ls`, `cat`, `pwd` yes. Be conservative, and note that
  `git` is *not* read-only, because of `git push`.
- process-**group** kill on timeout, or a killed `npm test` orphans node processes holding
  ports and file locks
- cap output at ~64 KB
- ship an unbypassable default deny-list. It does not stop a determined attacker; it stops
  an *accident*, which is far more common.

**Gate:** a 10-second timeout leaves **zero** orphan processes, verified per platform.

---

### Then · Casbin and auth · after Step 12

Now you have an HTTP surface, and can add users, roles and tenants. See §1.

---

## 3. Testing, throughout

Write the test in the same session as the code — not after. The gates are how you know a
step is finished, so a step without its test is not finished.

```
tests/agent/
├── test_contracts.py      Step 1  — path traversal, spec validation
├── test_base.py           Step 2  — run() never raises
├── test_tools_read.py     Step 3
├── test_repair.py         Step 6  — real malformed output from real small models
├── test_executor.py       Step 6  — batching, circuit breaker
├── test_preconditions.py  Step 7  — stale-edit refusal
└── test_permissions.py    Step 8  — the full decision matrix
```

**Collect real failures as fixtures.** When a small model emits malformed JSON in your
Step 4 loop, paste it into `test_repair.py`. That file becomes your most valuable asset —
a record of what your actual models actually do wrong, which no amount of reasoning from
first principles will give you.

---

## 4. If you remember only four things

1. **Errors are prompts.** Whatever a failed tool returns is the model's next input.
2. **Nothing escapes as an exception.** Every terminal state is a readable `ToolResult`.
3. **Nothing below the graph layer imports LangGraph.** ~1800 ms, and near-impossible to
   retrofit once twenty modules exist.
4. **`read_only` and `concurrency_safe` are load-bearing**, not documentation. They decide
   what runs in parallel, what is cached, and what needs approval.

---

← [Index](README.md)
