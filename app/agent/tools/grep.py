"""grep -- search file CONTENTS. Step 5 · Phase 04.

grep("def handle_login") -> app/auth/routes.py:42, then one targeted read. That is
one round-trip where a read-only agent would have spent four. Against the Phase 00
budget of roundtrips <= 4, search is not a convenience -- it is most of the budget.
"""

# NOTE ->> Shares PRUNE_DIRS and the threading rule with glob.py -- see the notes there
# NOTE ->> before writing this one.


# ==============================================================================
# 1 · Two backends, and the fallback is NOT optional
# ==============================================================================

# NOTE ->> ripgrep when present (~10x faster on a real repo, .gitignore for free),
# NOTE ->> Python when not. A CLI that only works if the user happens to have `rg`
# NOTE ->> installed is broken for most users.
# NOTE ->> Detect ONCE, cache the result, degrade silently.
# NOTE ->> Report which backend ran in ToolResult.metadata["backend"] -- when the two
# NOTE ->> disagree you want to find out from a log, not from a bug report.
# NOTE ->> subprocess.run BLOCKS -- the rg call goes to a thread too.


# ==============================================================================
# 2 · GrepInput + spec
# ==============================================================================

# NOTE ->> extra="forbid". pattern: str min_length=1. path: str = ".".
# NOTE ->> glob: str | None = None -- "only search files matching, e.g. '*.py'".
# NOTE ->> output_mode: OutputMode = CONTENT. case_insensitive: bool = False.
# NOTE ->> context_lines: int = 0, ge=0, le=10. limit: int = 100, ge=1, le=300.
# NOTE ->> spec: SEARCH, SAFE, read_only=True, concurrency_safe=True,
# NOTE ->>       timeout_s=30.0, budget_ms=250.

# NOTE ->> THREE OUTPUT MODES, because they cost wildly different token counts:
# NOTE ->>   content            -> file:line:text   (default -- usually what is wanted)
# NOTE ->>   files_with_matches -> paths only       ("which files mention X" -- far cheaper)
# NOTE ->>   count              -> per-file counts  ("how widespread is this")
# NOTE ->> Making the model choose is what keeps a broad search from costing a narrow one.


# ==============================================================================
# 3 · Four traps, each of which has bitten somebody
# ==============================================================================

# NOTE ->> (a) `rg` EXITS 1 FOR "NO MATCHES". That is a valid empty result, not a failure:
# NOTE ->>         if proc.returncode not in (0, 1): return None   # genuine failure -> fallback
# NOTE ->>     Treating exit 1 as an error makes the tool report failure on every successful
# NOTE ->>     no-match search.
# NOTE ->> (b) OutputMode is a StrEnum, so pydantic renders it as a $ref into $defs, NOT an
# NOTE ->>     inline enum. Step 6's coercion looks for an inline enum and will miss
# NOTE ->>     "CONTENT" -> "content". This is the tool that surfaces it; note it there.
# NOTE ->> (c) SKIP HUGE FILES in the Python fallback -- one 200 MB log will happily eat the
# NOTE ->>     entire timeout:  if path.stat().st_size > 4 * 1024 * 1024: continue
# NOTE ->> (d) Open with errors="ignore". A binary file that slipped past the glob filter
# NOTE ->>     should be skipped, not crash the whole scan.


# ==============================================================================
# 4 · Validate the regex BEFORE running it
# ==============================================================================

# NOTE ->> re.compile in a try, and return the re.error message VERBATIM.
# NOTE ->> "unbalanced parenthesis at position 12" is exactly the actionable error Step 6
# NOTE ->> wants -- an error message is a prompt, and this one is already written for you.


# ==============================================================================
# Gate  ->  tests/agent/test_tools_search.py
# ==============================================================================

# NOTE ->> grep p95 <= 250 ms on this repo.
# NOTE ->> the rg and Python backends return IDENTICAL results on a fixture set.
# NOTE ->> rg absent -> falls back silently, metadata["backend"] == "python".
# NOTE ->> no match -> a clear empty result, NOT an error.
# NOTE ->> invalid regex -> the re.error message, verbatim.
# NOTE ->> refuses paths outside the project.
