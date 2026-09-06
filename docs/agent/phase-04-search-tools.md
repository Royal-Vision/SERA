# Phase 04 — Search Tools

**Effort:** 1 day · **Depends on:** [03](phase-03-read-tool.md)
**The biggest single determinant of how many turns a task takes.**

---

## 1. Why this phase exists

Give an agent only `read_file` and watch it work:

```
read_file("main.py")        → not here
read_file("app.py")         → not here
read_file("src/app.py")     → not here
read_file("app/routes.py")  → found it
```

Four round-trips, ~6 seconds, and a context window full of files that were irrelevant.
With search:

```
grep("def handle_login")    → app/auth/routes.py:42
read_file("app/auth/routes.py", start_line=30)
```

One round-trip. Against the Phase 00 budget of `roundtrips ≤ 4`, search is not a
convenience — it is most of the budget.

**This is why tool *quality* beats tool *count*.** Two good search tools eliminate more
round-trips than twenty mediocre ones.

---

## 2. The architecture decision

### Two tools, not one

`glob` finds files **by name**; `grep` searches **contents**. Keeping them separate lets
the model express which it wants, and each has a schema tight enough to be hard to
misuse. A single fused `search` tool with a mode flag would be one more thing for the
model to get wrong.

### ripgrep when present, Python when not

| | ripgrep | Python fallback |
|---|---|---|
| Speed | ~10× on a real repo | baseline |
| `.gitignore` | free | manual pruning |
| Availability | not guaranteed | always |

**The fallback is not optional.** A CLI that only works when the user happens to have
`rg` installed is broken for most users. Detect once, cache the result, degrade silently.

Report which backend ran in `ToolResult.metadata` — when the two disagree, you want to
know.

### Recency ordering

`glob` sorts **newest first**. When an agent asks "where are the route files", the ones
touched recently are almost always the relevant ones. Since results are truncated, the
ordering decides what survives — and recency puts the useful answer above the cut.

---

## 3. The pruning problem

This is the performance story of the phase.

`.venv` on this repo holds **>40 000 files**. An unpruned `**/*.py` walk is the
difference between ~40 ms and several seconds — and worse, it fills the result list with
`site-packages` noise that crowds out the project's own code.

```python
PRUNE_DIRS = frozenset({
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "dist", "build", ".next", ".tox",
    ".idea", ".vscode", "site-packages", ".eggs", "htmlcov",
})
```

**`Path.glob` cannot prune.** It has no hook to skip a directory mid-walk, so a `**`
pattern descends into `.venv` regardless. Hand-roll `os.walk` and mutate `dirnames` in
place:

```python
for dirpath, dirnames, filenames in os.walk(root):
    dirnames[:] = [d for d in dirnames
                   if d not in PRUNE_DIRS and not d.startswith(".")]
```

The `dirnames[:]` slice assignment is what makes it work — `os.walk` reads the list back
to decide where to descend. Rebinding `dirnames` instead of mutating it silently does
nothing, which is a fun afternoon to lose.

Pruning is also a **security** measure: it keeps `.git` and `.env`-bearing directories
out of search results by default, which is a Tier-0 guardrail in the
[Phase 12](phase-12-guardrails.md) sense.

---

## 4. Threading

`os.walk` and a regex scan over a repository are **blocking CPU + syscall work**. In an
`async def` they stall the event loop, and once tools run in parallel batches one glob
freezes every sibling call.

```python
matches = await anyio.to_thread.run_sync(_walk_and_match, root, pattern, limit)
```

The subprocess call to `rg` goes to a thread too — `subprocess.run` blocks.

Under `asyncio` debug mode, no callback should exceed 50 ms.

---

## 5. What to build

### `glob`

```python
class GlobInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pattern: str = Field(min_length=1, description="Glob pattern, e.g. '**/*.py'")
    path: str = Field(default=".", description="Directory to search from")
    limit: int = Field(default=200, ge=1, le=500)

spec = ToolSpec(name="glob", risk=SAFE, read_only=True,
                concurrency_safe=True, timeout_s=15.0, budget_ms=120)
```

**Pattern normalisation.** Models write `*.py`, `**/*.py` and `src/**/*.py`
interchangeably. Handle all three: if the pattern has no `/` after leading `**/`, match
against the basename; otherwise match the relative path both with and without the `**/`
prefix.

**Bound the work, not just the output.** Collect up to `limit * 4` candidates before
sorting, so the recency sort has something to choose from without walking a monorepo to
completion.

### `grep`

```python
class GrepInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pattern: str = Field(min_length=1)
    path: str = Field(default=".")
    glob: str | None = Field(default=None, description="Only search files matching, e.g. '*.py'")
    output_mode: OutputMode = Field(default=OutputMode.CONTENT)
    case_insensitive: bool = False
    context_lines: int = Field(default=0, ge=0, le=10)
    limit: int = Field(default=100, ge=1, le=300)

spec = ToolSpec(name="grep", risk=SAFE, read_only=True,
                concurrency_safe=True, timeout_s=30.0, budget_ms=250)
```

**Three output modes**, because they cost very different numbers of tokens:

| Mode | Returns | Use |
|---|---|---|
| `content` | `file:line:text` | default — the model usually wants the line |
| `files_with_matches` | paths only | "which files mention X" — far cheaper |
| `count` | per-file counts | "how widespread is this" |

**Validate the regex before running it**, and return the `re.error` verbatim. It is
exactly the actionable message [Phase 05](phase-05-tool-engine.md) §5 wants.

---

## 6. Traps

**`rg` exits 1 for "no matches".** That is a valid empty result, not a failure:

```python
if proc.returncode not in (0, 1):
    return None          # genuine failure → fall back to Python
```

Treating exit 1 as an error makes the tool report failure on every successful
no-match search.

**`OutputMode` is a `StrEnum`, so Pydantic renders it as a `$ref` into `$defs`.** This is
the trap that bites in Phase 05: coercion looking for an inline `enum` will miss
`"CONTENT"` → `"content"`. Noted here because this is the tool that surfaces it.

**Skip huge files in the Python fallback.** A 200 MB log file will happily consume the
whole timeout:

```python
if path.stat().st_size > 4 * 1024 * 1024: continue
```

**Open with `errors="ignore"`.** Binary files that slipped past the glob filter should be
skipped, not crash the scan.

---

## 7. Gate

- [ ] `glob("**/*.py")` on this repo: p95 ≤ 120 ms, **zero** `.venv` results
- [ ] `glob` returns newest-first
- [ ] `grep` p95 ≤ 250 ms on this repo
- [ ] ripgrep and Python backends return **identical** results on a fixture set
- [ ] `rg` absent → falls back silently, `metadata["backend"] == "python"`
- [ ] No match → clear empty result, not an error
- [ ] Invalid regex → the `re.error` message, verbatim
- [ ] Event loop never blocked > 50 ms under `asyncio` debug mode
- [ ] Both tools refuse paths outside the project

---

← [Previous: Phase 03 — First Tool](phase-03-read-tool.md) · [Index](README.md) · [Next: Phase 05 — Tool Engine](phase-05-tool-engine.md) →
