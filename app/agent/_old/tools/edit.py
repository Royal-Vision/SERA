"""EditFileTool -- step 3 of the build order in `docs/tools.md`.

Exact string replacement, not line numbers or diffs. Reasons, in order of importance:

  1. Line numbers go stale the moment anything above the edit changes. Models routinely
     produce a correct edit against a line number that has already moved.
  2. A unique-match requirement is a *free correctness check*: if `old_string` appears
     twice, the model's mental model of the file is wrong, and failing loudly is far
     better than editing the wrong occurrence.
  3. Unified diffs need fuzzy hunk matching to be usable, and fuzzy matching on source
     code silently produces wrong results.

Guarded by the read-before-edit state machine in `engine/preconditions.py`.
"""

from __future__ import annotations

import anyio
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent.base import Tool
from app.agent.contracts import (
    AgentContext,
    RiskLevel,
    ToolCategory,
    ToolResult,
    ToolSpec,
)
from app.agent.engine.preconditions import tracker_for


class EditFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="File to edit, relative to the project root")
    old_string: str = Field(
        description=(
            "Exact text to replace, including indentation. Must appear exactly once "
            "unless replace_all is true. Include surrounding context to disambiguate."
        )
    )
    new_string: str = Field(description="Replacement text")
    replace_all: bool = Field(
        default=False, description="Replace every occurrence instead of requiring uniqueness"
    )

    @model_validator(mode="after")
    def _must_differ(self) -> "EditFileInput":
        if self.old_string == self.new_string:
            raise ValueError("old_string and new_string are identical -- nothing to do")
        return self


class EditFileTool(Tool[EditFileInput]):
    """Replace exact text in an existing file."""

    input_model = EditFileInput
    spec = ToolSpec(
        name="edit_file",
        category=ToolCategory.FILESYSTEM,
        risk=RiskLevel.MEDIUM,
        read_only=False,
        concurrency_safe=False,
        plan_mode_safe=False,
        timeout_s=15.0,
        budget_ms=60,
        description=(
            "Replace exact text in a file you have already read. old_string must match "
            "the file byte-for-byte, including indentation, and must be unique unless "
            "replace_all is set."
        ),
    )

    def permission_key(self, args: EditFileInput) -> str:
        return f"edit_file({args.path})"

    async def call(self, args: EditFileInput, ctx: AgentContext) -> ToolResult:
        try:
            path = ctx.resolve_in_project(args.path)
        except ValueError as exc:
            return ToolResult.error(str(exc))

        if not await anyio.Path(path).is_file():
            return ToolResult.error(
                f"File not found: {args.path}. Use write_file to create it."
            )

        tracker = tracker_for(ctx)
        if (problem := tracker.check_editable(path)) is not None:
            return ToolResult.error(problem)

        raw = await anyio.Path(path).read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return ToolResult.error(f"{args.path} is not valid UTF-8 text.")

        count = text.count(args.old_string)
        if count == 0:
            return ToolResult.error(
                f"old_string not found in {args.path}. It must match byte-for-byte, "
                f"including indentation and line endings. Re-read the file and copy the "
                f"exact text you want to replace."
            )
        if count > 1 and not args.replace_all:
            return ToolResult.error(
                f"old_string appears {count} times in {args.path}. Add surrounding "
                f"context to make it unique, or set replace_all=true to change all "
                f"{count} occurrences."
            )

        updated = (
            text.replace(args.old_string, args.new_string)
            if args.replace_all
            else text.replace(args.old_string, args.new_string, 1)
        )

        # Preserve the file's existing newline convention. Rewriting a CRLF file with
        # LF endings produces a diff touching every line, which is unreviewable.
        newline = "\r\n" if "\r\n" in text else "\n"
        encoded = updated.encode("utf-8")
        if newline == "\r\n":
            encoded = updated.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")

        await anyio.Path(path).write_bytes(encoded)
        tracker.record_read(path, encoded)

        replaced = count if args.replace_all else 1
        delta = updated.count("\n") - text.count("\n")
        return ToolResult.ok(
            f"Edited {args.path}: {replaced} replacement"
            f"{'s' if replaced != 1 else ''}, {delta:+d} lines.",
            replacements=replaced,
            line_delta=delta,
        )
