# Phase 03 — The First Tool

**Effort:** 0.5 day · **Depends on:** [02](phase-02-tool-contract.md)

---

## 1. Why this phase exists

`docs/tools.md` is explicit about starting here, and the reason is worth stating:

> File-read and search tools let you test the full agent/tool loop with a much smaller
> security risk.

`read_file` exercises every stage — schema generation, argument validation, permission
decision, path confinement, execution, `ToolResult` — while being read-only and confined
to the project. If the loop is broken, you find out now, on the tool where a bug costs
nothing.

Building `bash` first is the classic mistake. You end up debugging the executor and the
sandbox simultaneously, with a tool that can delete the repo while you do it.

---

## 2. The architecture decision

### Numbered lines, always

```
     1	def add(a, b):
     2	    return a - b
```

Not because the model needs them to read, but because they are the **shared coordinate
system** between `read_file` and everything the model says afterwards. "The bug is on
line 2" is only meaningful if line numbers were in the output.

Note the deliberate tension with [Phase 06](phase-06-mutation-tools.md): `edit_file`
takes *exact strings*, not line numbers, because line numbers go stale. Numbers are for
**discussion**; exact strings are for **mutation**. Both, for different jobs.

### Truncation is a feature

```python
MAX_BYTES = 256 * 1024
MAX_LINE_CHARS = 2_000
```

An agent that slurps a 10 MB file poisons its own context window, and — because tool
output is re-sent every turn — keeps paying for it until the session ends. A single
unbounded read can consume the entire remaining budget for a task.

Truncate, and **say so**, with the information needed to continue:

```
[2400 more lines; continue at start_line=2001]
```

That closing note is a prompt. Without it the model does not know it saw a partial file,
and will confidently reason about code it never read.

### Binary detection

```python
if b"\x00" in raw[:8192]:
    return ToolResult.error(f"{path} appears to be binary ({size} bytes).")
```

A NUL byte in the first block is the cheapest reliable signal. Checking the whole file
costs more and adds nothing.

---

## 3. What to build

```python
class ReadFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(description="Path to a UTF-8 text file, relative to the project root")
    start_line: int = Field(default=1, ge=1, description="1-indexed first line to return")
    max_lines: int = Field(default=2_000, ge=1, le=10_000)

class ReadFileTool(Tool[ReadFileInput]):
    input_model = ReadFileInput
    spec = ToolSpec(
        name="read_file",
        category=ToolCategory.FILESYSTEM,
        risk=RiskLevel.SAFE,
        read_only=True,
        concurrency_safe=True,
        timeout_s=10.0,
        budget_ms=25,
        cache_ttl_s=None,     # deliberate — see below
        description=("Read a text file from the project. Returns numbered lines so you "
                     "can reference them in a later edit_file call."),
    )
```

### Why `cache_ttl_s=None`

`read_file` is read-only, so `ToolSpec` *permits* caching. We decline it: files change
under us, and a cached read would serve stale content into an edit. This is the one place
where "read-only" and "cacheable" come apart, and it is worth a comment in the code so
nobody helpfully adds a TTL later.

### Implementation order matters

```python
path = ctx.resolve_in_project(args.path)     # 1. confine FIRST
if not await anyio.Path(path).is_file(): ... # 2. existence
raw = await anyio.Path(path).read_bytes()    # 3. read
tracker_for(ctx).record_read(path, raw)      # 4. record BEFORE truncation
if len(raw) > MAX_BYTES: raw = raw[:MAX_BYTES]
if b"\x00" in raw[:8192]: ...                # 5. binary check
text = raw.decode("utf-8", errors="replace") # 6. decode
```

**Step 4 is the one people get wrong**, and it is silent when they do. The read-before-edit
tracker ([Phase 05](phase-05-tool-engine.md) §8) hashes what was read. Record the
**full** bytes, before truncation — otherwise the hash never matches what `edit_file`
compares against, and every edit is rejected with "you must read the file first" *after
the model has just read it.*

**Decode leniently.** Truncation can split a multi-byte character, so
`errors="replace"` rather than failing.

---

## 4. Async style

Use `anyio.Path`, not `pathlib.Path`, for the I/O. A synchronous `read_bytes()` inside an
`async def` blocks the event loop — and once tools run in parallel batches
([Phase 05](phase-05-tool-engine.md) §6), one blocking read stalls every sibling call in
the batch.

This is cheap to get right now and annoying to retrofit across six tools later.

---

## 5. Gate

- [ ] Returns numbered lines from `start_line`, honouring `max_lines`
- [ ] Truncates over 256 KB and reports how many bytes were dropped
- [ ] Clips lines over 2000 chars with a `[+N chars]` marker
- [ ] Rejects binary files
- [ ] Refuses `../../etc/passwd` and absolute paths outside the project
- [ ] Refuses symlinks pointing outside the project
- [ ] `start_line` past EOF returns an actionable error, not an empty result
- [ ] Empty file returns a clear message, not an empty string
- [ ] Records the full pre-truncation bytes with the file-state tracker
- [ ] p95 ≤ 25 ms on a warm file
- [ ] Unknown parameter → validation error, not silent ignore

---

← [Previous: Phase 02 — Tool Contract](phase-02-tool-contract.md) · [Index](README.md) · [Next: Phase 04 — Search Tools](phase-04-search-tools.md) →
