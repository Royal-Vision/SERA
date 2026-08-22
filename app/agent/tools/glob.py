"""Glob -- P0 · fs.search. Step 5 · Phase 04 + Tool Catalog §P0.

Search is the biggest single determinant of how many turns a task takes, so it is
the biggest determinant of latency and cost -- more than any model choice. Without
it the model probes: read("main.py"), read("app.py"), read("src/app.py")... four
round-trips and a context window full of files that were irrelevant.

Default target decision: ALLOW inside the approved workspace.
"""

# NOTE ->> Two tools, not one. Glob = by NAME, Grep = by CONTENT. A fused search tool with
# NOTE ->> a mode flag is one more thing for the model to get wrong; two tight schemas are
# NOTE ->> each hard to misuse.


# ==============================================================================
# 1 · PRUNE_DIRS  --  the performance story of this phase
# ==============================================================================

# NOTE ->> frozenset: .git, .venv, venv, node_modules, __pycache__, .mypy_cache,
# NOTE ->> .pytest_cache, .ruff_cache, dist, build, .next, .tox, .idea, .vscode,
# NOTE ->> site-packages, .eggs, htmlcov
# NOTE ->> .venv on THIS repo holds >40 000 files. Unpruned `**/*.py` is ~40 ms vs several
# NOTE ->> seconds -- and worse, site-packages noise crowds the project's own code out of a
# NOTE ->> truncated result list.
# NOTE ->> Pruning is also a Tier-0 GUARDRAIL: it keeps .git and .env-bearing directories
# NOTE ->> out of results by default. Catalog: "ignore rules and protected directories apply
# NOTE ->> BEFORE traversal" -- prune on the way down, never filter on the way out.


# ==============================================================================
# 2 · The walk  --  why not Path.glob
# ==============================================================================

# NOTE ->> Path.glob CANNOT prune. No hook to skip a directory mid-walk, so ** descends into
# NOTE ->> .venv no matter what you do afterwards. Hand-roll os.walk:
# NOTE ->>     for dirpath, dirnames, filenames in os.walk(root):
# NOTE ->>         dirnames[:] = [d for d in dirnames
# NOTE ->>                        if d not in PRUNE_DIRS and not d.startswith(".")]
# NOTE ->> The dirnames[:] SLICE ASSIGNMENT is what makes it work -- os.walk reads that list
# NOTE ->> back to decide where to descend. Rebinding `dirnames = [...]` silently does
# NOTE ->> nothing, and that is a fun afternoon to lose.
# NOTE ->> NO SYMLINK ESCAPE (catalog): os.walk(followlinks=False) is the default -- keep it,
# NOTE ->> and resolve each hit before emitting it so a symlinked FILE cannot leak a path
# NOTE ->> outside the root either.


# ==============================================================================
# 3 · Threading + cancellation
# ==============================================================================

# NOTE ->> os.walk is blocking CPU + syscall work. In an async def it stalls the event loop,
# NOTE ->> and once Step 6 batches tools one glob freezes every sibling call.
# NOTE ->>     matches = await anyio.to_thread.run_sync(_walk_and_match, root, pattern, limit)
# NOTE ->> Under asyncio debug mode no callback may exceed 50 ms.
# NOTE ->> CATALOG ->> "cancellation during traversal". A thread cannot be killed, so the walk
# NOTE ->> must poll a cancel flag / anyio.CancelScope between directories -- otherwise a
# NOTE ->> cancelled turn keeps a thread walking a monorepo to completion.


# ==============================================================================
# 4 · Input + output
# ==============================================================================

# NOTE ->> extra="forbid". pattern: str, min_length=1, e.g. "**/*.py".
# NOTE ->> path: str = "." -- the search ROOT, defaults to workspace cwd.
#
# CONFLICT ->> default result limit: Phase 04 says 200 (le=500), catalog says 100. Pick one.
# CONFLICT ->> Recommendation: 100. It is the smaller context bill, and pagination below
# CONFLICT ->> makes the cap cheap to step past when the model actually needs more.
#
# NOTE ->> OUTPUT is a record, not a bare list -- catalog requires all four:
# NOTE ->>   duration_ms, num_files, filenames (ordered), truncated
# NOTE ->> `truncated` is the one that matters: without it the model treats a capped list as
# NOTE ->> the complete answer and concludes the file does not exist.
# NOTE ->> Relativise paths under cwd -- shorter, and it stops absolute paths leaking the
# NOTE ->> machine's directory layout into the transcript.
# NOTE ->> spec: SEARCH, SAFE, read_only=True, concurrency_safe=True,
# NOTE ->>       timeout_s=15.0, budget_ms=120.


# ==============================================================================
# 5 · Ordering, pagination, pattern shapes
# ==============================================================================

# NOTE ->> PATTERN NORMALISATION. Models write "*.py", "**/*.py" and "src/**/*.py"
# NOTE ->> interchangeably and mean the same thing. Handle all three: no "/" after a leading
# NOTE ->> "**/" -> match the BASENAME; otherwise match the relative path both with and
# NOTE ->> without the "**/" prefix. Cheaper than making Step 6 repair the pattern.

# NOTE ->> RECENCY ORDER: newest mtime first. Results are truncated, so the ordering decides
# NOTE ->> what SURVIVES the cut -- and the recently-touched file is almost always the one
# NOTE ->> being asked about.
# NOTE ->> Bound the WORK, not just the output: collect up to limit * 4 candidates before
# NOTE ->> sorting, so the sort has something to choose from without walking a monorepo dry.
# NOTE ->> DETERMINISM (catalog): mtime ties are common -- checkout writes a whole tree in
# NOTE ->> the same second. Break ties on the relative path so two identical calls cannot
# NOTE ->> return two different orders, or pagination silently skips and repeats files.
# NOTE ->> PAGINATION CURSOR: opaque, encoding (last_mtime, last_path). Not a numeric offset
# NOTE ->> -- the tree changes between calls and an offset would slide.


# ==============================================================================
# Gate  ->  tests/agent/test_tools_search.py
# ==============================================================================

# NOTE ->> glob("**/*.py") on this repo: p95 <= 120 ms, and ZERO .venv results.
# NOTE ->> newest-first, with path as a deterministic tie-break.
# NOTE ->> two identical calls return identical ordering.
# NOTE ->> a symlinked directory pointing outside the root is not followed.
# NOTE ->> refuses a search root outside the project.
# NOTE ->> truncated=True is set whenever the cap was hit.
# NOTE ->> cancellation mid-traversal actually stops the walk.
# NOTE ->> the event loop is never blocked > 50 ms under asyncio debug mode.
