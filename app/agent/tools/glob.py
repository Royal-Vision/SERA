"""glob -- find files by NAME. Step 5 · Phase 04.

Search is the biggest single determinant of how many turns a task takes, so it is
the biggest determinant of latency and cost -- more than any model choice. Without
it the model probes: read_file("main.py"), read_file("app.py"), read_file("src/app.py")
... four round-trips and a context window full of files that were irrelevant.
"""

# NOTE ->> Two tools, not one. glob = by name, grep = by content. A fused search tool
# NOTE ->> with a mode flag is one more thing for the model to get wrong; two tight
# NOTE ->> schemas are each hard to misuse.


# ==============================================================================
# 1 · PRUNE_DIRS  --  the performance story of this phase
# ==============================================================================

# NOTE ->> frozenset: .git, .venv, venv, node_modules, __pycache__, .mypy_cache,
# NOTE ->> .pytest_cache, .ruff_cache, dist, build, .next, .tox, .idea, .vscode,
# NOTE ->> site-packages, .eggs, htmlcov
# NOTE ->> .venv on THIS repo holds >40 000 files. Unpruned `**/*.py` is ~40 ms vs several
# NOTE ->> seconds -- and worse, site-packages noise crowds the project's own code out of
# NOTE ->> a truncated result list.
# NOTE ->> Pruning is also a Tier-0 GUARDRAIL: it keeps .git and .env-bearing directories
# NOTE ->> out of search results by default.


# ==============================================================================
# 2 · The walk  --  why not Path.glob
# ==============================================================================

# NOTE ->> Path.glob CANNOT prune. It has no hook to skip a directory mid-walk, so a **
# NOTE ->> pattern descends into .venv no matter what you do afterwards. Hand-roll os.walk:
# NOTE ->>     for dirpath, dirnames, filenames in os.walk(root):
# NOTE ->>         dirnames[:] = [d for d in dirnames
# NOTE ->>                        if d not in PRUNE_DIRS and not d.startswith(".")]
# NOTE ->> The dirnames[:] SLICE ASSIGNMENT is what makes it work -- os.walk reads that list
# NOTE ->> back to decide where to descend. Rebinding `dirnames = [...]` instead silently
# NOTE ->> does nothing, and that is a fun afternoon to lose.


# ==============================================================================
# 3 · Threading
# ==============================================================================

# NOTE ->> os.walk is blocking CPU + syscall work. In an async def it stalls the event loop,
# NOTE ->> and once Step 6 batches tools one glob freezes every sibling call.
# NOTE ->>     matches = await anyio.to_thread.run_sync(_walk_and_match, root, pattern, limit)
# NOTE ->> Under asyncio debug mode no callback may exceed 50 ms.


# ==============================================================================
# 4 · GlobInput + spec
# ==============================================================================

# NOTE ->> extra="forbid". pattern: str, min_length=1, e.g. "**/*.py".
# NOTE ->> path: str = ".". limit: int = 200, ge=1, le=500.
# NOTE ->> spec: SEARCH, SAFE, read_only=True, concurrency_safe=True,
# NOTE ->>       timeout_s=15.0, budget_ms=120.


# ==============================================================================
# 5 · Two behaviours worth getting right
# ==============================================================================

# NOTE ->> PATTERN NORMALISATION. Models write "*.py", "**/*.py" and "src/**/*.py"
# NOTE ->> interchangeably and mean the same thing. Handle all three: no "/" after a leading
# NOTE ->> "**/" -> match the BASENAME; otherwise match the relative path both with and
# NOTE ->> without the "**/" prefix. Cheaper than making Step 6 repair the pattern.

# NOTE ->> RECENCY ORDER. Sort newest-first by mtime. Results are truncated, so the ordering
# NOTE ->> decides what SURVIVES the cut -- and the recently-touched file is almost always
# NOTE ->> the one being asked about.
# NOTE ->> Bound the WORK, not just the output: collect up to limit * 4 candidates before
# NOTE ->> sorting, so the sort has something to choose from without walking a monorepo dry.


# ==============================================================================
# Gate  ->  tests/agent/test_tools_search.py
# ==============================================================================

# NOTE ->> glob("**/*.py") on this repo: p95 <= 120 ms, and ZERO .venv results.
# NOTE ->> results come back newest-first.
# NOTE ->> refuses paths outside the project (route through ctx.resolve_in_project).
# NOTE ->> the event loop is never blocked > 50 ms under asyncio debug mode.
