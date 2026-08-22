"""read_file -- the first real tool. Step 3 · Phase 03.

Chosen first because it exercises every stage of the pipeline -- schema, validation,
permission, path confinement, execution, ToolResult -- on the smallest possible
security surface. Building bash first means debugging the executor and the sandbox
at once, with a tool that can delete the repo while you do it.
"""

# NOTE ->> anyio.Path for the I/O, NOT pathlib.Path. A sync read_bytes() inside an
# NOTE ->> async def blocks the event loop, and once Step 6 batches tools in parallel one
# NOTE ->> blocking read stalls every sibling in the batch. Cheap now, painful to retrofit.


# ==============================================================================
# 1 · Limits
# ==============================================================================

# NOTE ->> MAX_BYTES = 256 * 1024. MAX_LINE_CHARS = 2_000.
# NOTE ->> Truncation is a FEATURE, not a safety net. Tool output is re-sent every turn,
# NOTE ->> so one unbounded read keeps costing until the session ends -- a 10 MB file can
# NOTE ->> eat the whole remaining budget for the task.


# ==============================================================================
# 2 · ReadFileInput
# ==============================================================================

# NOTE ->> model_config = ConfigDict(extra="forbid"). ALWAYS. Models hallucinate params;
# NOTE ->> silent acceptance produces wrong behaviour, loud rejection produces a correction.
# NOTE ->> path: str       -- Field(description=...), described as project-relative.
# NOTE ->> start_line: int -- default 1, ge=1. 1-indexed, because the OUTPUT is 1-indexed.
# NOTE ->> max_lines: int  -- default 2_000, ge=1, le=10_000.
# NOTE ->> Constraints belong in the schema: Step 6 renders ge/le into error messages, so
# NOTE ->> le=10_000 becomes "max_lines must be <= 10000, you sent 50000" for free.


# ==============================================================================
# 3 · ReadFileTool
# ==============================================================================

# NOTE ->> spec: name="read_file", FILESYSTEM, SAFE, read_only=True, concurrency_safe=True,
# NOTE ->>       timeout_s=10.0, budget_ms=25, cache_ttl_s=None.
# NOTE ->> cache_ttl_s=None is DELIBERATE and needs the comment saying so. ToolSpec permits
# NOTE ->> caching here (read_only), but files change under us and a cached read would serve
# NOTE ->> stale content straight into an edit. This is the one place where read-only and
# NOTE ->> cacheable come apart -- say it in the code so nobody helpfully adds a TTL.
# NOTE ->> description: tell the model the output is NUMBERED and that those numbers are what
# NOTE ->> a later edit_file call refers to. That sentence is what makes the tools compose.


# ==============================================================================
# 4 · call()  --  the order IS the correctness
# ==============================================================================

# NOTE ->> 1. path = ctx.resolve_in_project(args.path)   -- CONFINE FIRST, before touching disk.
# NOTE ->> 2. exists / is_file                           -- missing file -> actionable error.
# NOTE ->> 3. raw = await anyio.Path(path).read_bytes()
# NOTE ->> 4. record the read with the file-state tracker in ctx.extras -- FULL bytes, BEFORE
# NOTE ->>    truncation. THIS is the step people get wrong and it fails SILENTLY: Step 7
# NOTE ->>    hashes what was read, so recording truncated bytes means the hash never matches
# NOTE ->>    and every edit is refused with "read the file first" -- immediately after the
# NOTE ->>    model read it.
# NOTE ->> 5. truncate to MAX_BYTES
# NOTE ->> 6. binary check: b"\x00" in raw[:8192] -> refuse. A NUL in the first block is the
# NOTE ->>    cheapest reliable signal; scanning the whole file costs more and adds nothing.
# NOTE ->>    A PNG in the context window is pure waste.
# NOTE ->> 7. text = raw.decode("utf-8", errors="replace") -- LENIENT: truncation can split a
# NOTE ->>    multi-byte character, and crashing there would be absurd.


# ==============================================================================
# 5 · Output shape
# ==============================================================================

# NOTE ->> Numbered lines: right-aligned number + TAB + text. Not to help the model read --
# NOTE ->> they are the shared COORDINATE SYSTEM between this tool and every later sentence
# NOTE ->> about the file. "The bug is on line 2" means nothing without them.
# NOTE ->> Deliberate tension with Step 7: edit_file takes EXACT STRINGS, never line numbers,
# NOTE ->> because numbers go stale the moment anything above them changes.
# NOTE ->> Numbers are for DISCUSSION, exact strings are for MUTATION. Both, different jobs.
# NOTE ->> Clip any line over MAX_LINE_CHARS with a [+N chars] marker.
# NOTE ->> When truncated, the closing note IS a prompt -- it must carry the way forward:
# NOTE ->>     [2400 more lines; continue at start_line=2001]
# NOTE ->> Without it the model does not know its view was partial, and will reason
# NOTE ->> confidently about code it never saw.
# NOTE ->> Set ToolResult.truncated to how much was cut, so the engine knows too.
# NOTE ->> Empty file and start_line-past-EOF each need their OWN message: an empty string
# NOTE ->> back from a successful call is indistinguishable from a bug.


# ==============================================================================
# Gate  ->  tests/agent/test_tools_read.py
# ==============================================================================

# NOTE ->> numbered lines from start_line, honouring max_lines.
# NOTE ->> over 256 KB -> truncated, and the result says how many bytes were dropped.
# NOTE ->> a line over 2000 chars -> clipped with [+N chars].
# NOTE ->> a binary file -> refused.
# NOTE ->> ../../etc/passwd, an absolute path outside the project, a symlink out -> refused.
# NOTE ->> start_line past EOF -> actionable error, not an empty result.
# NOTE ->> empty file -> a clear message, not "".
# NOTE ->> the tracker holds the FULL pre-truncation bytes.
# NOTE ->> unknown parameter -> validation error, not silently ignored.
# NOTE ->> p95 <= 25 ms on a warm file  (that is budget_ms, asserted).
