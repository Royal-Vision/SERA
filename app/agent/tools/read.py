"""Read -- P0 · fs.read. Step 3 · Phase 03 + Tool Catalog §P0.

First real tool because it exercises every stage -- schema, validation, permission,
path confinement, execution, ToolResult -- on the smallest security surface. Build
bash first and you debug the executor and the sandbox at once, with a tool that can
delete the repo while you do it.

Default target decision: ALLOW inside the approved workspace.
"""

# NOTE ->> anyio.Path for I/O, NOT pathlib.Path. A sync read_bytes() in an async def
# NOTE ->> blocks the loop; once Step 6 batches tools, one blocking read stalls every
# NOTE ->> sibling in the batch. Cheap now, painful to retrofit across six tools.


# ==============================================================================
# 1 · Input   (CONFLICT -- resolve before writing the model)
# ==============================================================================

# NOTE ->> model_config = ConfigDict(extra="forbid"). ALWAYS. Models hallucinate params;
# NOTE ->> silent acceptance produces wrong behaviour, loud rejection produces a correction.
#
# CONFLICT ->> Catalog says:      file_path: str, offset: int >= 0, limit: int > 0, pages: str
# CONFLICT ->> Phase 03 says:     path: str,      start_line: int >= 1, max_lines: int
# CONFLICT ->> These are not cosmetic. offset is 0-based, start_line is 1-based, and the
# CONFLICT ->> OUTPUT is 1-based numbered lines either way -- so one of them needs an
# CONFLICT ->> off-by-one at the boundary. Pick ONE and write it down here.
# CONFLICT ->> Recommendation: take the catalog's names (file_path/offset/limit) -- they are
# CONFLICT ->> the schema real models are already trained on -- and keep offset 0-based,
# CONFLICT ->> converting once at the numbering step.
#
# NOTE ->> Constraints belong in the schema: Step 6 renders ge/le into error messages,
# NOTE ->> so le=10_000 becomes "limit must be <= 10000, you sent 50000" for free.


# ==============================================================================
# 2 · Output  --  a TAGGED UNION, not a string
# ==============================================================================

# NOTE ->> Catalog requires one variant per content kind, discriminated:
# NOTE ->>   text      -> numbered lines + line metadata
# NOTE ->>   image     -> MIME + base64 + dimensions
# NOTE ->>   notebook  -> cells
# NOTE ->>   pdf       -> bytes, or an extracted per-page artifact set
# NOTE ->>   unchanged -> the marker, when the fingerprint matches a prior read
# NOTE ->> SCOPE ->> Phase 03 builds ONLY the text variant. Ship text first; leave the
# NOTE ->> discriminator field in from day one so adding image/pdf later is an addition
# NOTE ->> and not a breaking reshape of every call site.
# NOTE ->> Large text: STREAM or page it. Binary: return an artifact REFERENCE, never
# NOTE ->> the bytes -- a PNG in the context window is pure waste.


# ==============================================================================
# 3 · Limits
# ==============================================================================

# NOTE ->> MAX_BYTES = 256 * 1024. MAX_LINE_CHARS = 2_000.
# NOTE ->> Truncation is a FEATURE. Tool output is re-sent every turn, so one unbounded
# NOTE ->> read keeps costing until the session ends -- a 10 MB file can eat the whole
# NOTE ->> remaining budget for the task.


# ==============================================================================
# 4 · Spec
# ==============================================================================

# NOTE ->> name="read_file" (catalog canonical name: "Read"), FILESYSTEM, SAFE,
# NOTE ->> read_only=True, concurrency_safe=True, timeout_s=10.0, budget_ms=25,
# NOTE ->> cache_ttl_s=None.
# NOTE ->> cache_ttl_s=None is DELIBERATE -- say so in the code. ToolSpec PERMITS caching
# NOTE ->> here because read_only, but files change under us and a cached read would serve
# NOTE ->> stale content straight into an edit. This is the one place read-only and
# NOTE ->> cacheable come apart, and the catalog's "unchanged marker" is the right answer
# NOTE ->> instead: re-read, compare fingerprint, return the marker when nothing moved.
# NOTE ->> description: say the output is NUMBERED and that those numbers are what a later
# NOTE ->> edit_file call refers to. That sentence is what makes the tools compose.


# ==============================================================================
# 5 · call()  --  the order IS the correctness
# ==============================================================================

# NOTE ->> 1. path = ctx.resolve_in_project(args.file_path)  -- CONFINE FIRST, before disk.
# NOTE ->> 2. reject non-regular files BEFORE metadata access: device paths, FIFOs, UNC.
# NOTE ->>    Catalog calls out "hanging device paths" -- opening /dev/zero or a named pipe
# NOTE ->>    blocks forever and burns the whole turn budget on a stat that never returns.
# NOTE ->> 3. exists / is_file                              -- missing -> actionable error.
# NOTE ->> 4. raw = await anyio.Path(path).read_bytes()
# NOTE ->> 5. RECORD THE FINGERPRINT -- (path, identity, mtime, size, hash/range) -- with
# NOTE ->>    FULL bytes, BEFORE truncation. THIS is the step people get wrong and it fails
# NOTE ->>    SILENTLY: Step 7 compares this hash, so recording truncated bytes means the
# NOTE ->>    hash never matches and every edit is refused with "read the file first" --
# NOTE ->>    immediately after the model read it.
# NOTE ->>    "identity" = st_dev + st_ino, not the path string: a rename must invalidate.
# NOTE ->> 6. truncate to MAX_BYTES
# NOTE ->> 7. binary check: b"\x00" in raw[:8192] -> artifact reference, not bytes.
# NOTE ->>    A NUL in the first block is the cheapest reliable signal; scanning the whole
# NOTE ->>    file costs more and adds nothing.
# NOTE ->> 8. text = raw.decode("utf-8", errors="replace") -- LENIENT: truncation can split
# NOTE ->>    a multi-byte character, and crashing there would be absurd.


# ==============================================================================
# 6 · Output shape
# ==============================================================================

# NOTE ->> Numbered lines: right-aligned number + TAB + text. Not to help the model read --
# NOTE ->> they are the shared COORDINATE SYSTEM between this tool and every later sentence
# NOTE ->> about the file. "The bug is on line 2" means nothing without them.
# NOTE ->> Deliberate tension with Step 7: edit_file takes EXACT STRINGS, never line numbers,
# NOTE ->> because numbers go stale the moment anything above them changes.
# NOTE ->> Numbers are for DISCUSSION, exact strings are for MUTATION. Both, different jobs.
# NOTE ->> Clip any line over MAX_LINE_CHARS with a [+N chars] marker.
# NOTE ->> When truncated, the closing note IS a prompt -- carry the way forward:
# NOTE ->>     [2400 more lines; continue at offset=2000]
# NOTE ->> Without it the model does not know its view was partial and will reason
# NOTE ->> confidently about code it never saw.
# NOTE ->> Set ToolResult.truncated to how much was cut.
# NOTE ->> Empty file and offset-past-EOF each need their OWN message: an empty string back
# NOTE ->> from a successful call is indistinguishable from a bug.


# ==============================================================================
# 7 · Permission + concurrency
# ==============================================================================

# NOTE ->> Allow ONLY after canonical containment AND read-rule evaluation. Deny or require
# NOTE ->> explicit policy for: protected config, secret-bearing files, device paths, UNC,
# NOTE ->> anything outside the workspace. Containment alone is not the whole check --
# NOTE ->> .env is inside the project and still must not be read on a whim.
# NOTE ->> Concurrency: SHARED read lock keyed on canonical file IDENTITY (dev+ino), so a
# NOTE ->> concurrent Edit on the same file serialises against it. concurrency_safe=True
# NOTE ->> means "parallel with other reads", never "parallel with a write".


# ==============================================================================
# Gate  ->  tests/agent/test_tools_read.py
# ==============================================================================

# NOTE ->> numbered lines from offset, honouring limit.
# NOTE ->> over 256 KB -> truncated, and the result says how many bytes were dropped.
# NOTE ->> a line over 2000 chars -> clipped with [+N chars].
# NOTE ->> a binary file -> artifact reference, not bytes.
# NOTE ->> ../../etc/passwd, an absolute path outside the project, a symlink out -> refused.
# NOTE ->> a device path / FIFO -> refused WITHOUT blocking.
# NOTE ->> offset past EOF -> actionable error, not an empty result.
# NOTE ->> empty file -> a clear message, not "".
# NOTE ->> the fingerprint holds the FULL pre-truncation bytes and survives a rename check.
# NOTE ->> unknown parameter -> validation error, not silently ignored.
# NOTE ->> p95 <= 25 ms on a warm file  (that is budget_ms, asserted).
