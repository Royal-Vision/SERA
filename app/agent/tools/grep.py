"""Grep -- P0 · fs.search. Step 5 · Phase 04 + Tool Catalog §P0.

grep("def handle_login") -> app/auth/routes.py:42, then one targeted read. One
round-trip where a read-only agent would have spent four. Against the Phase 00
budget of roundtrips <= 4, search is not a convenience -- it is most of the budget.

Default target decision: ALLOW inside the approved workspace.
"""

# NOTE ->> Shares PRUNE_DIRS, the threading rule and the cancellation rule with glob.py --
# NOTE ->> read those notes first.


# ==============================================================================
# 1 · Two backends, and the fallback is NOT optional
# ==============================================================================

# NOTE ->> ripgrep when present (~10x faster on a real repo, .gitignore for free),
# NOTE ->> Python when not. A CLI that only works if the user happens to have `rg` installed
# NOTE ->> is broken for most users. Detect ONCE, cache, degrade silently.
# NOTE ->> Report the backend in ToolResult.metadata["backend"] -- when the two disagree you
# NOTE ->> want to learn it from a log, not a bug report.
#
# NOTE ->> ARGUMENT VECTOR, NEVER A SHELL STRING (catalog, and this is the security item in
# NOTE ->> this file): subprocess.run([rg, "-e", pattern, ...], shell=False). The pattern is
# NOTE ->> attacker-influenced text from a model. Interpolated into a shell string, a pattern
# NOTE ->> containing $(...) or ; is command execution -- from a tool declared read_only,
# NOTE ->> SAFE, and auto-allowed in DEFAULT mode. Use -e so a pattern starting with "-" is
# NOTE ->> a pattern and not a flag.
# NOTE ->> subprocess.run BLOCKS -- the rg call goes to a thread too.


# ==============================================================================
# 2 · Input
# ==============================================================================

# NOTE ->> extra="forbid". pattern: str, min_length=1, REQUIRED regex.
# NOTE ->> path: str = "." (search root). glob: str | None -- "only files matching, e.g. *.py".
# NOTE ->> output_mode: OutputMode = CONTENT.
# NOTE ->> case_insensitive: bool = False. multiline: bool = False. type: str | None (rg -t).
# NOTE ->> context: -A / -B / -C, each ge=0 le=10.  head_limit, offset -> pagination.
#
# CONFLICT ->> default cap: Phase 04 says limit=100 (le=300), catalog says 250. Pick one.
# CONFLICT ->> Recommendation: keep 100 as the DEFAULT and raise the ceiling to 250 -- the
# CONFLICT ->> default is a token bill paid every call, the ceiling is paid only on request.
#
# NOTE ->> head_limit=0 is PRIVILEGED, not ordinary input (catalog): it means unbounded.
# NOTE ->> A model must not be able to send it -- either exclude 0 from the model schema
# NOTE ->> (ge=1) and accept it only from internal callers, or it becomes the one call that
# NOTE ->> dumps a whole repo into the context window.
# NOTE ->> spec: SEARCH, SAFE, read_only=True, concurrency_safe=True,
# NOTE ->>       timeout_s=30.0, budget_ms=250.


# ==============================================================================
# 3 · Output modes  --  they cost wildly different token counts
# ==============================================================================

# NOTE ->>   content            -> file:line:text   (default -- usually what is wanted)
# NOTE ->>   files_with_matches -> paths only       ("which files mention X" -- far cheaper)
# NOTE ->>   count              -> per-file counts  ("how widespread is this")
# NOTE ->> Making the model choose is what keeps a broad search from costing a narrow one.
# NOTE ->> Output record: mode, file count, filenames, optional content, line/match counts,
# NOTE ->> and the applied pagination metadata. Echo the pagination back -- the model cannot
# NOTE ->> ask for page 2 if it was never told which page it got.


# ==============================================================================
# 4 · Five traps, each of which has bitten somebody
# ==============================================================================

# NOTE ->> (a) `rg` EXITS 1 FOR "NO MATCHES". A valid empty result, not a failure:
# NOTE ->>         if proc.returncode not in (0, 1): return None   # real failure -> fallback
# NOTE ->>     Treating exit 1 as an error makes the tool report failure on every successful
# NOTE ->>     no-match search.
# NOTE ->> (b) OutputMode is a StrEnum, so pydantic renders it as a $ref into $defs, NOT an
# NOTE ->>     inline enum. Step 6's coercion looks for an inline enum and will miss
# NOTE ->>     "CONTENT" -> "content". This is the tool that surfaces it.
# NOTE ->> (c) SKIP HUGE FILES in the Python fallback -- one 200 MB log eats the whole
# NOTE ->>     timeout:  if path.stat().st_size > 4 * 1024 * 1024: continue
# NOTE ->> (d) Open with errors="ignore". A binary file that slipped past the glob filter
# NOTE ->>     should be skipped, not crash the scan.
# NOTE ->> (e) CATASTROPHIC BACKTRACKING. `(a+)+$` against a long line hangs the regex engine
# NOTE ->>     inside your thread, where timeout_s cannot reach it -- Python's re has no
# NOTE ->>     step limit. rg (Rust regex) is linear-time and immune; the PYTHON FALLBACK is
# NOTE ->>     the exposed one. Bound line length before matching.


# ==============================================================================
# 5 · Validate the regex BEFORE running it
# ==============================================================================

# NOTE ->> re.compile in a try, and return the re.error message VERBATIM.
# NOTE ->> "unbalanced parenthesis at position 12" is exactly the actionable error Step 6
# NOTE ->> wants -- an error message is a prompt, and this one is already written for you.
# NOTE ->> Reject what the backend cannot support rather than silently differing: rg and
# NOTE ->> Python disagree on lookbehind and on some escapes, and the gate below asserts the
# NOTE ->> two backends return IDENTICAL results.


# ==============================================================================
# 6 · Permission
# ==============================================================================

# NOTE ->> Same root and ignore policy as Read. The catalog states the rule that matters:
# NOTE ->> REGEX CONTENT DOES NOT WIDEN FILESYSTEM SCOPE. The pattern chooses what matches,
# NOTE ->> never where to look -- only `path` does that, and only after containment.
# NOTE ->> Concurrency: shared lock on the search root.


# ==============================================================================
# Gate  ->  tests/agent/test_tools_search.py
# ==============================================================================

# NOTE ->> grep p95 <= 250 ms on this repo.
# NOTE ->> rg and Python backends return IDENTICAL results on a fixture set.
# NOTE ->> rg absent -> falls back silently, metadata["backend"] == "python".
# NOTE ->> no match -> a clear empty result, NOT an error.
# NOTE ->> invalid regex -> the re.error message, verbatim.
# NOTE ->> a pattern containing `;` / `$(...)` / a leading `-` executes nothing and is
# NOTE ->>   treated as a literal pattern.
# NOTE ->> a catastrophic-backtracking pattern does not hang the fallback past timeout_s.
# NOTE ->> head_limit=0 from the model schema is rejected.
# NOTE ->> refuses a search root outside the project.
