"""GrepTool -- content search.

Uses ripgrep when it is on PATH and falls back to a pure-Python scan otherwise.
The fast path matters: ripgrep is typically an order of magnitude faster than a
Python `re` walk on a real repository, because it is parallel, SIMD-accelerated and
respects .gitignore for free.

The fallback is not optional. A CLI agent that only works when the user happens to
have ripgrep installed is a CLI agent that is broken for most users.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from enum import StrEnum
from pathlib import Path

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
from app.agent.tools.glob import PRUNE_DIRS

MAX_MATCHES = 300


class OutputMode(StrEnum):
    CONTENT = "content"
    FILES = "files_with_matches"
    COUNT = "count"


class GrepInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern: str = Field(description="Regular expression to search for", min_length=1)
    path: str = Field(default=".", description="File or directory to search")
    glob: str | None = Field(
        default=None, description="Only search files matching this glob, e.g. '*.py'"
    )
    output_mode: OutputMode = Field(default=OutputMode.CONTENT)
    case_insensitive: bool = Field(default=False)
    context_lines: int = Field(default=0, ge=0, le=10)
    limit: int = Field(default=100, ge=1, le=MAX_MATCHES)


class GrepTool(Tool[GrepInput]):
    """Search file contents with a regular expression."""

    input_model = GrepInput
    spec = ToolSpec(
        name="grep",
        category=ToolCategory.SEARCH,
        risk=RiskLevel.SAFE,
        read_only=True,
        concurrency_safe=True,
        timeout_s=30.0,
        budget_ms=250,
        description=(
            "Search file contents with a regular expression. Prefer this over reading "
            "files one by one when you are looking for where something is defined or used."
        ),
    )

    def permission_key(self, args: GrepInput) -> str:
        return f"grep({args.pattern})"

    async def call(self, args: GrepInput, ctx: AgentContext) -> ToolResult:
        try:
            target = ctx.resolve_in_project(args.path)
        except ValueError as exc:
            return ToolResult.error(str(exc))

        if not target.exists():
            return ToolResult.error(f"Path not found: {args.path}")

        try:
            re.compile(args.pattern)
        except re.error as exc:
            return ToolResult.error(f"Invalid regex {args.pattern!r}: {exc}")

        rg = _ripgrep_path()
        if rg:
            out = await anyio.to_thread.run_sync(_run_ripgrep, rg, target, args)
            if out is not None:
                return self._render(out, args, ctx, backend="ripgrep")

        out = await anyio.to_thread.run_sync(_run_python_scan, target, args)
        return self._render(out, args, ctx, backend="python")

    def _render(
        self, hits: "list[tuple[Path, int, str]]", args: GrepInput,
        ctx: AgentContext, backend: str,
    ) -> ToolResult:
        if not hits:
            return ToolResult.ok(
                f"No matches for {args.pattern!r} in {args.path}.", count=0, backend=backend
            )

        def rel(p: Path) -> str:
            try:
                return str(p.relative_to(ctx.cwd)).replace(os.sep, "/")
            except ValueError:
                return str(p)

        if args.output_mode is OutputMode.FILES:
            seen = list(dict.fromkeys(rel(p) for p, _, _ in hits))
            body = "\n".join(seen[: args.limit])
            return ToolResult.ok(body, count=len(seen), backend=backend)

        if args.output_mode is OutputMode.COUNT:
            counts: dict[str, int] = {}
            for p, _, _ in hits:
                counts[rel(p)] = counts.get(rel(p), 0) + 1
            body = "\n".join(f"{n:6d}  {f}" for f, n in
                             sorted(counts.items(), key=lambda kv: -kv[1])[: args.limit])
            return ToolResult.ok(body, count=len(counts), backend=backend)

        shown = hits[: args.limit]
        body = "\n".join(f"{rel(p)}:{n}:{line}" for p, n, line in shown)
        if len(hits) > args.limit:
            body += f"\n\n[{len(hits) - args.limit} more matches; narrow the pattern]"
        return ToolResult.ok(body, count=len(hits), backend=backend)


_RG_CACHE: "str | None | bool" = False


def _ripgrep_path() -> "str | None":
    global _RG_CACHE
    if _RG_CACHE is False:
        _RG_CACHE = shutil.which("rg")
    return _RG_CACHE  # type: ignore[return-value]


def _run_ripgrep(rg: str, target: Path, args: GrepInput) -> "list[tuple[Path, int, str]] | None":
    """Returns None if ripgrep could not run, so the caller falls back."""
    cmd = [rg, "--line-number", "--no-heading", "--color", "never", "--max-count", "50"]
    if args.case_insensitive:
        cmd.append("-i")
    if args.glob:
        cmd += ["--glob", args.glob]
    if args.context_lines:
        cmd += ["-C", str(args.context_lines)]
    cmd += ["--", args.pattern, str(target)]

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=25, encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    # rg exits 1 for "no matches" -- that is a valid empty result, not a failure.
    if proc.returncode not in (0, 1):
        return None

    out: list[tuple[Path, int, str]] = []
    for line in proc.stdout.splitlines():
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        fname, lineno, text = parts
        try:
            out.append((Path(fname), int(lineno), text))
        except ValueError:
            continue
    return out


def _run_python_scan(target: Path, args: GrepInput) -> "list[tuple[Path, int, str]]":
    from fnmatch import fnmatch

    flags = re.IGNORECASE if args.case_insensitive else 0
    rx = re.compile(args.pattern, flags)
    hits: list[tuple[Path, int, str]] = []

    files: list[Path] = []
    if target.is_file():
        files = [target]
    else:
        for dirpath, dirnames, filenames in os.walk(target):
            dirnames[:] = [d for d in dirnames if d not in PRUNE_DIRS and not d.startswith(".")]
            for f in filenames:
                if args.glob and not fnmatch(f, args.glob):
                    continue
                files.append(Path(dirpath) / f)

    for path in files:
        try:
            if path.stat().st_size > 4 * 1024 * 1024:
                continue
            with path.open("r", encoding="utf-8", errors="ignore") as fh:
                for n, line in enumerate(fh, 1):
                    if rx.search(line):
                        hits.append((path, n, line.rstrip("\n")[:500]))
                        if len(hits) >= MAX_MATCHES:
                            return hits
        except (OSError, UnicodeDecodeError):
            continue
    return hits
