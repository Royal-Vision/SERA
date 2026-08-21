"""ReadFileTool -- the first tool, per the build order in `docs/tools.md`.

Read-only and confined to the project, so it exercises the whole validate ->
authorize -> execute -> ToolResult loop with minimal security surface.
"""

from __future__ import annotations

import anyio
from pydantic import BaseModel, ConfigDict, Field

from app.agent.base import Tool
from app.agent.engine.preconditions import tracker_for
from app.agent.contracts import (
    AgentContext,
    RiskLevel,
    ToolCategory,
    ToolResult,
    ToolSpec,
)

#: Read at most this much before truncating. An agent that slurps a 10 MB file
#: poisons its own context window and every subsequent turn pays for it.
MAX_BYTES = 256 * 1024
MAX_LINE_CHARS = 2_000


class ReadFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="Path to a UTF-8 text file, relative to the project root")
    start_line: int = Field(default=1, ge=1, description="1-indexed first line to return")
    max_lines: int = Field(default=2_000, ge=1, le=10_000)


class ReadFileTool(Tool[ReadFileInput]):
    """Read a text file from the current project."""

    input_model = ReadFileInput
    spec = ToolSpec(
        name="read_file",
        category=ToolCategory.FILESYSTEM,
        risk=RiskLevel.SAFE,
        read_only=True,
        concurrency_safe=True,
        timeout_s=10.0,
        budget_ms=25,
        cache_ttl_s=None,  # files change under us; caching would serve stale content
        description=(
            "Read a text file from the project. Returns numbered lines so you can "
            "reference them in a later edit_file call."
        ),
    )

    def permission_key(self, args: ReadFileInput) -> str:
        return f"read_file({args.path})"

    async def call(self, args: ReadFileInput, ctx: AgentContext) -> ToolResult:
        try:
            path = ctx.resolve_in_project(args.path)
        except ValueError as exc:
            return ToolResult.error(str(exc))

        if not await anyio.Path(path).exists():
            return ToolResult.error(f"File not found: {args.path}")
        if not await anyio.Path(path).is_file():
            return ToolResult.error(f"Not a regular file: {args.path}")

        stat = await anyio.Path(path).stat()
        raw = await anyio.Path(path).read_bytes()

        # Feed the read-before-edit state machine. Recorded on the FULL bytes, before
        # any truncation, so the hash matches what edit_file will compare against.
        tracker_for(ctx).record_read(path, raw)

        truncated_bytes = 0
        if len(raw) > MAX_BYTES:
            truncated_bytes = len(raw) - MAX_BYTES
            raw = raw[:MAX_BYTES]

        # A NUL byte in the first block is the cheapest reliable binary signal.
        if b"\x00" in raw[:8192]:
            return ToolResult.error(
                f"{args.path} appears to be binary ({stat.st_size} bytes). "
                "Only UTF-8 text files can be read."
            )

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            # Truncation may have split a multi-byte character; retry leniently
            # before declaring the file unreadable.
            text = raw.decode("utf-8", errors="replace")

        lines = text.splitlines()
        start = args.start_line - 1
        if start >= len(lines) and lines:
            return ToolResult.error(
                f"start_line {args.start_line} is past the end of {args.path} "
                f"({len(lines)} lines)."
            )

        selected = lines[start : start + args.max_lines]
        rendered = "\n".join(
            f"{n:6d}\t{_clip(line)}"
            for n, line in enumerate(selected, start=args.start_line)
        )

        remaining = len(lines) - (start + len(selected))
        notes = []
        if remaining > 0:
            notes.append(f"{remaining} more lines; continue at start_line={start + len(selected) + 1}")
        if truncated_bytes:
            notes.append(f"{truncated_bytes} bytes truncated (file exceeds {MAX_BYTES // 1024} KB)")

        if not rendered:
            return ToolResult.ok(f"{args.path} is empty.", lines=0)

        if notes:
            rendered += "\n\n[" + "; ".join(notes) + "]"

        return ToolResult.ok(
            rendered,
            lines=len(selected),
            total_lines=len(lines),
            bytes=stat.st_size,
        )


def _clip(line: str) -> str:
    if len(line) <= MAX_LINE_CHARS:
        return line
    return line[:MAX_LINE_CHARS] + f"… [+{len(line) - MAX_LINE_CHARS} chars]"
