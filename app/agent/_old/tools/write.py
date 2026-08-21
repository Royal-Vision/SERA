"""WriteFileTool -- create a file, or fully replace one.

Overwriting a file the agent has never read is the single easiest way to destroy work,
so it is refused: an existing file must be read first. Creating a *new* file has no such
requirement, because there is nothing to lose.
"""

from __future__ import annotations

import anyio
from pydantic import BaseModel, ConfigDict, Field

from app.agent.base import Tool
from app.agent.contracts import (
    AgentContext,
    RiskLevel,
    ToolCategory,
    ToolResult,
    ToolSpec,
)
from app.agent.engine.preconditions import tracker_for

MAX_WRITE_BYTES = 5 * 1024 * 1024


class WriteFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="File to write, relative to the project root")
    content: str = Field(description="Complete file contents")
    create_dirs: bool = Field(
        default=True, description="Create missing parent directories"
    )


class WriteFileTool(Tool[WriteFileInput]):
    """Create a new file, or fully replace one you have read."""

    input_model = WriteFileInput
    spec = ToolSpec(
        name="write_file",
        category=ToolCategory.FILESYSTEM,
        risk=RiskLevel.MEDIUM,
        read_only=False,
        concurrency_safe=False,
        plan_mode_safe=False,
        timeout_s=15.0,
        budget_ms=60,
        description=(
            "Write a complete file. Use for new files, or to fully replace a file you "
            "have already read. For partial changes use edit_file instead -- it is "
            "safer and far cheaper in tokens."
        ),
    )

    def permission_key(self, args: WriteFileInput) -> str:
        return f"write_file({args.path})"

    def risk_for(self, args: WriteFileInput) -> RiskLevel:
        # Creating a new file is recoverable; clobbering an existing one is not.
        return self.spec.risk

    async def call(self, args: WriteFileInput, ctx: AgentContext) -> ToolResult:
        try:
            path = ctx.resolve_in_project(args.path)
        except ValueError as exc:
            return ToolResult.error(str(exc))

        encoded = args.content.encode("utf-8")
        if len(encoded) > MAX_WRITE_BYTES:
            return ToolResult.error(
                f"Refusing to write {len(encoded) // 1024} KB to {args.path}: "
                f"the limit is {MAX_WRITE_BYTES // 1024} KB."
            )

        apath = anyio.Path(path)
        existed = await apath.is_file()
        tracker = tracker_for(ctx)

        if existed:
            if (problem := tracker.check_editable(path)) is not None:
                return ToolResult.error(
                    problem.replace("before editing", "before overwriting")
                )
        elif await apath.exists():
            return ToolResult.error(f"{args.path} exists and is not a regular file.")

        if args.create_dirs:
            await anyio.Path(path.parent).mkdir(parents=True, exist_ok=True)
        elif not await anyio.Path(path.parent).is_dir():
            return ToolResult.error(
                f"Parent directory does not exist: {path.parent.name}. "
                f"Set create_dirs=true to create it."
            )

        await apath.write_bytes(encoded)
        tracker.record_read(path, encoded)

        lines = args.content.count("\n") + 1
        verb = "Replaced" if existed else "Created"
        return ToolResult.ok(
            f"{verb} {args.path} ({lines} lines, {len(encoded)} bytes).",
            created=not existed,
            bytes=len(encoded),
            lines=lines,
        )
