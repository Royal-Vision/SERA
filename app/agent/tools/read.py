"""Read -- P0 · fs.read. Step 3 · Phase 03 + Tool Catalog §P0.

First real tool because it exercises every stage -- schema, validation, permission,
path confinement, execution, ToolResult -- on the smallest security surface. Build
bash first and you debug the executor and the sandbox at once, with a tool that can
delete the repo while you do it.

Default target decision: ALLOW inside the approved workspace.
"""

import anyio
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from typing import Annotated, Literal

from app.agent.base import BaseTool, ToolSemanticError
from app.agent.contracts import (
    ConcurrencyClass, Decision, Idempotency, InterruptBehavior, RiskLevel,
    SideEffect, TimeoutPolicy, ToolCategory, ToolRuntimeContext, ToolSpec,
)
from app.agent.tools._fs import (
    assert_regular_file, confine, file_state, fingerprint, is_binary, is_secret_path,
    relativise,
)

# ==============================================================================
# 3 · Limits  --  truncation is a FEATURE
# ==============================================================================

# Tool output is re-sent every turn, so one unbounded read keeps costing until the
# session ends -- a 10 MB file can eat the whole remaining budget for the task.
MAX_BYTES = 256 * 1024
MAX_LINE_CHARS = 2_000


# ==============================================================================
# 1 · Input   (CONFLICT -- resolved)
# ==============================================================================

# RESOLVED ->> Catalog names win: file_path / offset / limit, and offset is 0-BASED.
# RESOLVED ->> Phase 03 proposed path / start_line(1-based) / max_lines. These are not
# RESOLVED ->> cosmetic -- the OUTPUT is 1-based numbered lines either way, so one of them
# RESOLVED ->> has to carry an off-by-one at the boundary. The catalog spelling is what
# RESOLVED ->> real models are already trained on, so it costs the fewest corrections;
# RESOLVED ->> the 0->1 conversion happens ONCE, at the numbering step in _render.

class ReadArgs(BaseModel):
    """extra="forbid" ALWAYS. Models hallucinate params; silent acceptance produces
    wrong behaviour, loud rejection produces a correction."""

    model_config = ConfigDict(extra="forbid", strict=True)

    file_path: str = Field(min_length=1, description="Path to the file, inside the workspace.")
    offset: int = Field(default=0, ge=0, description="0-based line to start from.")
    # Constraints belong in the schema: the executor renders ge/le into error messages,
    # so le=10_000 becomes "limit must be <= 10000, you sent 50000" for free.
    limit: int = Field(default=2_000, gt=0, le=10_000, description="Max lines to return.")


# ==============================================================================
# 2 · Output  --  a TAGGED UNION, not a string
# ==============================================================================

# SCOPE ->> Phase 03 builds ONLY the text variant; binary falls back to a reference.
# SCOPE ->> image / notebook / pdf variants are additions later, NOT a breaking reshape
# SCOPE ->> of every call site -- which is why the discriminator is here from day one.

class TextContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["text"] = "text"
    path: str
    text: str
    """Numbered lines, ready to paste into a model turn."""
    first_line: int
    last_line: int
    total_lines: int
    truncated_bytes: int = 0
    clipped_lines: int = 0


class BinaryContent(BaseModel):
    """Binary: return a REFERENCE, never the bytes -- a PNG in the context window is
    pure waste, and the executor turns this into an ArtifactRef."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["binary"] = "binary"
    path: str
    size_bytes: int
    sha256: str


class UnchangedContent(BaseModel):
    """The catalog's answer to "why is read not cacheable": re-read, compare the
    fingerprint, and say nothing moved instead of re-sending the whole file."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["unchanged"] = "unchanged"
    path: str
    sha256: str


ReadOutput = Annotated[
    TextContent | BinaryContent | UnchangedContent,
    Field(discriminator="kind"),
]


# ==============================================================================
# 6 · Output shape
# ==============================================================================

def _render(text: str, offset: int, limit: int, path_label: str) -> tuple[str, int, int, int, int]:
    """Numbered lines: right-aligned number + TAB + text.

    Not to help the model read -- they are the shared COORDINATE SYSTEM between this
    tool and every later sentence about the file. "The bug is on line 2" means nothing
    without them.

    Deliberate tension with edit_file, which takes EXACT STRINGS and never line
    numbers: numbers go stale the moment anything above them changes. Numbers are for
    DISCUSSION, exact strings are for MUTATION.
    """
    lines = text.splitlines()
    total = len(lines)

    if total == 0:
        # An empty string back from a successful call is indistinguishable from a bug.
        return f"[{path_label} is empty -- 0 lines]", 0, 0, 0, 0

    if offset >= total:
        raise ToolSemanticError(
            f"offset {offset} is past the end of {path_label} ({total} lines).",
            remedy=f"Use an offset between 0 and {total - 1}.",
            total_lines=total,
        )

    window = lines[offset:offset + limit]
    width = len(str(offset + len(window)))
    clipped = 0
    out: list[str] = []

    for i, line in enumerate(window, start=offset + 1):   # <- the ONE 0->1 conversion
        if len(line) > MAX_LINE_CHARS:
            line = f"{line[:MAX_LINE_CHARS]} [+{len(line) - MAX_LINE_CHARS} chars]"
            clipped += 1
        out.append(f"{i:>{width}}\t{line}")

    remaining = total - (offset + len(window))
    if remaining > 0:
        # The closing note IS a prompt -- carry the way forward. Without it the model
        # does not know its view was partial and will reason confidently about code it
        # never saw.
        out.append(f"\n[{remaining} more lines; continue at offset={offset + len(window)}]")

    return "\n".join(out), offset + 1, offset + len(window), total, clipped


# ==============================================================================
# 5 · call()  --  the order IS the correctness
# ==============================================================================

class ReadTool(BaseTool[ReadArgs, ReadOutput]):

    # anyio.Path for I/O, NOT pathlib.Path. A sync read_bytes() in an async def blocks
    # the loop; once the executor batches tools, one blocking read stalls every sibling
    # in the batch. Cheap now, painful to retrofit across six tools.

    spec = ToolSpec[ReadArgs, ReadOutput](
        name="read_file",
        version="1.0.0",
        description=(
            "Read a file from the workspace. Output is NUMBERED lines -- those numbers "
            "are what a later edit_file call refers to when discussing the file, though "
            "edit_file itself matches on exact strings, never numbers. Large files are "
            "truncated; use offset to page through the rest."
        ),
        input_model=ReadArgs,
        output_adapter=TypeAdapter[ReadOutput](ReadOutput),
        category=ToolCategory.FILESYSTEM,
        side_effect=SideEffect.NONE,
        risk_level=RiskLevel.LOW,
        capabilities=frozenset({"fs.read"}),
        default_permission=Decision.ALLOW,
        # READ_PARALLEL, not PARALLEL: "parallel with other reads", never "parallel with
        # a write". A concurrent edit on the same file must serialise against this.
        concurrency=ConcurrencyClass.READ_PARALLEL,
        resource_keys=lambda args: (f"fs:{args.file_path}:read",),
        timeout=TimeoutPolicy(default_s=10.0, max_s=30.0),
        interrupt_behavior=InterruptBehavior.CANCEL,
        # DELIBERATELY not PURE, so cache_ttl_s cannot be set. ToolSpec would PERMIT
        # caching here because side_effect is none -- but files change under us and a
        # cached read would serve stale content straight into an edit. This is the one
        # place read-only and cacheable come apart; UnchangedContent is the right answer.
        idempotency=Idempotency.IDEMPOTENT,
        cache_ttl_s=None,
        max_inline_result_bytes=MAX_BYTES,
        aliases=("Read",),
    )

    async def validate_semantics(self, args: ReadArgs, ctx: ToolRuntimeContext) -> None:
        path = confine(ctx, args.file_path)          # 1. CONFINE FIRST, before disk.
        assert_regular_file(path)                    # 2. reject FIFO/device/dir, 3. exists.
        # Containment alone is not the whole check -- .env is inside the project.
        if is_secret_path(path):
            raise ToolSemanticError(
                f"{path.name} is a secret-bearing file.",
                remedy="Secrets are not readable by tools. Ask the user for what you need.",
                path=str(path),
            )

    def human_summary(self, args: ReadArgs) -> str:
        window = "" if args.offset == 0 else f" from line {args.offset + 1}"
        return f"Read {args.file_path}{window}"

    async def execute(self, args: ReadArgs, ctx: ToolRuntimeContext) -> ReadOutput:
        path = confine(ctx, args.file_path)
        st = assert_regular_file(path)
        label = relativise(path, ctx.workspace_root)

        raw = await anyio.Path(path).read_bytes()    # 4.

        # 5. RECORD THE FINGERPRINT with FULL bytes, BEFORE truncation. Getting this
        #    wrong fails silently: edit compares this hash, so a truncated hash means
        #    every edit is refused with "read the file first" right after the read.
        fp = fingerprint(path, raw, st)
        complete = args.offset == 0 and len(raw) <= MAX_BYTES
        state = file_state(ctx)
        state.record_read(fp, complete=complete)

        # The catalog's answer to "why is read not cacheable" (see the spec block on
        # cache_ttl_s): re-read, compare, and send back a marker instead of the whole
        # file. Only when a PREVIOUS read_file actually delivered these bytes to the
        # model -- an edit or write recorded in the same turn does not count, because
        # the model never saw that content.
        view = (args.offset, args.limit)
        seen = state.delivered(fp.identity, view)
        if seen is not None and seen.matches(fp):
            return UnchangedContent(path=label, sha256=fp.sha256)

        if is_binary(raw):                            # 7. before any decode
            return BinaryContent(path=label, size_bytes=st.st_size, sha256=fp.sha256)

        truncated = max(0, len(raw) - MAX_BYTES)
        body = raw[:MAX_BYTES]                        # 6.

        # 8. LENIENT decode: truncation can split a multi-byte character, and crashing
        #    there would be absurd.
        text = body.decode("utf-8", errors="replace")

        state.record_delivery(fp, view)

        rendered, first, last, total, clipped = _render(text, args.offset, args.limit, label)
        if truncated:
            rendered += f"\n[{truncated} bytes beyond the {MAX_BYTES // 1024} KB cap were not read]"

        return TextContent(
            path=label, text=rendered, first_line=first, last_line=last,
            total_lines=total, truncated_bytes=truncated, clipped_lines=clipped,
        )


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
