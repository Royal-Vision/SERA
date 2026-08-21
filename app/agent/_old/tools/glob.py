"""GlobTool -- step 2 of the build order in `docs/tools.md`.

Fast path matching, sorted newest-first. Recency ordering matters more than it looks:
when an agent asks "where are the route files", the ones touched most recently are
almost always the relevant ones, and putting them first means the useful answer
survives truncation.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import anyio
import orjson
from pydantic import BaseModel, ConfigDict, Field

from app.agent.base import Tool
from app.agent.contracts import (
    AgentContext,
    RiskLevel,
    ToolCategory,
    ToolResult,
    ToolSpec,
)

MAX_RESULTS = 500

#: Never descend into these. Scanning .venv or node_modules can take seconds and
#: returns nothing an agent wants -- on this project .venv alone holds >40k files.
PRUNE_DIRS = frozenset(
    {
        ".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache",
        ".pytest_cache", ".ruff_cache", "dist", "build", ".next", ".tox",
        ".idea", ".vscode", "site-packages", ".eggs", "htmlcov",
    }
)


class GlobInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern: str = Field(
        description="Glob pattern, e.g. '**/*.py' or 'app/**/routes.py'",
        min_length=1,
    )
    path: str = Field(
        default=".",
        description="Directory to search from, relative to the project root",
    )
    limit: int = Field(default=200, ge=1, le=MAX_RESULTS)


class GlobTool(Tool[GlobInput]):
    """Find files by name pattern, newest first."""

    input_model = GlobInput
    spec = ToolSpec(
        name="glob",
        category=ToolCategory.SEARCH,
        risk=RiskLevel.SAFE,
        read_only=True,
        concurrency_safe=True,
        timeout_s=15.0,
        budget_ms=120,
        description=(
            "Find files matching a glob pattern, sorted by modification time "
            "(newest first). Use this to locate files by name; use grep to search "
            "their contents."
        ),
    )

    def permission_key(self, args: GlobInput) -> str:
        return f"glob({args.pattern})"

    async def call(self, args: GlobInput, ctx: AgentContext) -> ToolResult:
        try:
            root = ctx.resolve_in_project(args.path)
        except ValueError as exc:
            return ToolResult.error(str(exc))

        if not await anyio.Path(root).is_dir():
            return ToolResult.error(f"Not a directory: {args.path}")

        # Walking a large tree is blocking CPU + syscalls. Off the event loop it goes,
        # or one glob freezes every other concurrent tool call.
        started = time.monotonic()
        try:
            matches = await anyio.to_thread.run_sync(
                _walk_and_match, root, args.pattern, args.limit
            )
        except ValueError as exc:
            return ToolResult.error(f"Invalid pattern {args.pattern!r}: {exc}")

        if not matches:
            return ToolResult.ok(
                f"No files matching {args.pattern!r} under {args.path}.", count=0
            )

        rel = [str(p.relative_to(ctx.cwd)).replace(os.sep, "/") for p in matches]
        body = "\n".join(rel)
        if len(matches) >= args.limit:
            body += f"\n\n[truncated at {args.limit}; narrow the pattern for more]"

        return ToolResult.ok(
            body,
            count=len(matches),
            elapsed_ms=round((time.monotonic() - started) * 1000, 1),
        )


def _walk_and_match(root: Path, pattern: str, limit: int) -> list[Path]:
    """Prune-as-you-go walk. Runs in a worker thread.

    `Path.glob` has no way to skip directories mid-walk, so a `**` pattern would
    descend into .venv regardless. Hand-rolling the walk lets us prune, which is the
    difference between ~40 ms and several seconds on this repo.
    """
    from fnmatch import fnmatch

    # Normalise so '**/*.py' and '*.py' both behave the way a user expects.
    pat = pattern.replace(os.sep, "/")
    match_basename = "/" not in pat.lstrip("*/")
    bare = pat.rsplit("/", 1)[-1] if match_basename else pat

    results: list[tuple[float, Path]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in PRUNE_DIRS and not d.startswith(".")]
        here = Path(dirpath)
        for fname in filenames:
            full = here / fname
            rel = str(full.relative_to(root)).replace(os.sep, "/")
            hit = fnmatch(fname, bare) if match_basename else (
                fnmatch(rel, pat) or fnmatch(rel, pat.removeprefix("**/"))
            )
            if not hit:
                continue
            try:
                results.append((full.stat().st_mtime, full))
            except OSError:
                continue
            if len(results) > limit * 4:  # bounded work, still enough to sort well
                break

    results.sort(key=lambda t: t[0], reverse=True)
    return [p for _, p in results[:limit]]

