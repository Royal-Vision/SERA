# SERA Agent — Build Plan

**Status:** v3 · **Last updated:** 2026-08-21

**What we are building:** the Python **agent backend** for a coding agent — a tool
engine, a permission model, a provider abstraction and an agent loop, exposed over
NDJSON on stdio.

**What we are not building here:** the CLI. That is React Ink (Node/TS), built
separately against the protocol defined in [Phase 01](phase-01-runtime.md).

**The one design constraint:** when a user signs in with Codex, Antigravity or Ollama,
the only thing they should feel is *their own LLM*. Everything SERA adds is invisible.

---

> **Lost, or starting the code?** Read **[BUILD-ORDER.md](BUILD-ORDER.md)** first. It
> sequences these phases into concrete files with a gate per step, and answers the
> Casbin question.

## How to read this

Each phase is one file, and each file answers four questions in order:

1. **Why this phase exists** — the story, and what breaks without it
2. **The architecture decision** — what we chose, what we rejected, and why
3. **What to build** — modules, signatures, implementation style
4. **The gate** — the measurable condition for moving on

Phases are ordered by dependency, not importance. Do not start one before its
predecessor's gate passes; the gates are what stop the latency contract from eroding one
convenient shortcut at a time.

---

## The phases

| # | Phase | Why it exists | Effort |
|---|---|---|---|
| **00** | [Architecture](phase-00-architecture.md) | **How to choose the architecture.** Competitive landscape, the layer model, the latency contract. Read before writing any code | 0.5 d |
| **01** | [Runtime & Protocol](phase-01-runtime.md) | A process that starts fast and speaks a stable protocol. Import discipline is set here or never | 0.5 d |
| **02** | [Tool Contract](phase-02-tool-contract.md) | The `ToolSpec` metadata every later phase reads. The most load-bearing 200 lines in the system | 1 d |
| **03** | [First Tool](phase-03-read-tool.md) | `read_file` — proves the whole loop on the smallest security surface | 0.5 d |
| **04** | [Search Tools](phase-04-search-tools.md) | `glob` + `grep`. The biggest single determinant of how many turns a task takes | 1 d |
| **05** | [**Tool Engine**](phase-05-tool-engine.md) | **Today.** Repair, dispatch, preconditions. ~75% of tool failures are recoverable without an LLM round-trip | 2 d |
| **06** | [Mutation Tools](phase-06-mutation-tools.md) | `edit_file` + `write_file`, with no path to silent data loss | 1 d |
| **07** | [Providers](phase-07-providers.md) | Codex / Antigravity / Ollama behind one warm registry | 1 d |
| **08** | [**LangGraph Architecture**](phase-08-langgraph.md) | **Today.** Graph vs. agent, why we hand-build, state design, multi-agent | 1 d |
| **09** | [Agent Loop](phase-09-agent-loop.md) | End to end: prompt in, tools run, tokens stream out | 2 d |
| **10** | [Sessions & Context](phase-10-sessions.md) | Long conversations that stay affordable and resumable | 1.5 d |
| **11** | [Permissions](phase-11-permissions.md) | The approval gate. Also the real defence against prompt injection | 1.5 d |
| **12** | [Guardrails & PII](phase-12-guardrails.md) | Why Hermes and OpenClaw stay small and fast: policy in the harness, not the weights | 1 d |
| **13** | [Deferred](phase-13-deferred.md) | Subagents, hooks, MCP, plugins — and why each waits | — |

**Total: ~14 days to a differentiated agent backend.**

**Appendix:** [Critique of the existing code](appendix-critique.md) — every file in the
repo today, rated, with the specific defects.

---

## Today's path

You are working on the tool engine and the LangGraph architecture. Read in this order:

1. **[Phase 00](phase-00-architecture.md)** — how to choose, and where the competitive
   opening is. It frames both.
2. **[Phase 05](phase-05-tool-engine.md)** — the engine. This is the phase that actually
   differentiates the product.
3. **[Phase 08](phase-08-langgraph.md)** — the graph, and why we do not use
   `create_agent`.

Phases 02–04 and 06–07 are prerequisites for *running* the engine, but you can read 05
and 08 first to make the architectural calls.

---

## The thesis in eight lines

1. **Import cost is the startup latency.** `import langgraph.graph` costs ~1800 ms
   (measured on this machine). Nothing below the graph layer may reach it.
2. **Errors are prompts.** Whatever a failed tool returns becomes the model's next
   input. `"ValidationError"` teaches nothing; the constraint plus valid values gets it
   right on the retry.
3. **Nothing escapes as an exception.** Every terminal state is a `ToolResult` the model
   can read and recover from. An exception reaching the loop kills the turn.
4. **`read_only` and `concurrency_safe` are load-bearing**, not documentation. They
   decide what runs in parallel, what is cached, and what needs approval.
5. **Warm everything.** Chat clients and the compiled graph are built once, never per
   invocation.
6. **One agent until proven otherwise.** When you fan out, use `Send` so it costs one
   call's wall clock rather than N.
7. **Guardrails live in the harness, not the weights.** That is why model size and
   policy are independent axes.
8. **Measure against a stub provider.** It is the only honest picture of what SERA
   itself costs.

---

## Environment

The venv is CPython **3.14.7**; `python` on PATH is **3.12.9**. Use
`.venv/Scripts/python.exe` explicitly, or every benchmark lies to you.

`winloop` is installed, so `install_event_loop_policy()` returns `winloop` rather than
the stdlib Proactor loop on Windows.

Scratch code from earlier sessions is untracked under `app/agent/` and `scripts/`. Treat
it as reference, not the build. `scripts/bench_runtime.py` is worth keeping regardless —
it is the harness behind every performance number in these documents.

Earlier drafts of these documents are archived in `_old/`.
