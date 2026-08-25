"""Glob -- P0 · fs.search. Step 5 · Phase 04 + Tool Catalog §P0.

Search is the biggest single determinant of how many turns a task takes, so it is
the biggest determinant of latency and cost -- more than any model choice. Without
it the model probes: read("main.py"), read("app.py"), read("src/app.py")... four
round-trips and a context window full of files that were irrelevant.

Default target decision: ALLOW inside the approved workspace.
"""

# Two tools, not one. Glob = by NAME, Grep = by CONTENT. A fused search tool with a
# mode flag is one more thing for the model to get wrong; two tight schemas are each
# hard to misuse.

import base64
import fnmatch
import os
import threading
import time
from pathlib import Path

import anyio
import anyio.to_thread
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.agent.base import BaseTool, ToolSemanticError
from app.agent.contracts import (
    ConcurrencyClass, Decision, Idempotency, InterruptBehavior, RiskLevel,
    SideEffect, TimeoutPolicy, ToolCategory, ToolRuntimeContext, ToolSpec,
)
from app.agent.tools._fs import confine, prunable, relativise

# CONFLICT ->> RESOLVED: default limit 100, ceiling 500.
# CONFLICT ->> Phase 04 said 200 (le=500), the catalog said 100. 100 is the smaller
# CONFLICT ->> context bill paid on EVERY call, and the pagination cursor below makes
# CONFLICT ->> stepping past it cheap when the model actually needs more.
DEFAULT_LIMIT = 100
MAX_LIMIT = 500

# Bound the WORK, not just the output: collect this multiple of `limit` before sorting,
# so the sort has something to choose from without walking a monorepo dry.
CANDIDATE_FACTOR = 4


# ==============================================================================
# 4 · Input + output
# ==============================================================================

class GlobArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    pattern: str = Field(min_length=1, description='Name pattern, e.g. "**/*.py".')
    path: str = Field(default=".", description="Search root; defaults to the workspace.")
    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)
    cursor: str | None = Field(
        default=None, description="Opaque cursor from a previous truncated result."
    )


class GlobOutput(BaseModel):
    """A record, not a bare list. `truncated` is the one that matters: without it the
    model treats a capped list as the complete answer and concludes the file does not
    exist."""

    model_config = ConfigDict(extra="forbid")

    duration_ms: float
    num_files: int
    filenames: list[str]
    truncated: bool
    cursor: str | None = None


# ==============================================================================
# 5 · Pattern normalisation
# ==============================================================================

def _matcher(pattern: str):
    """Models write "*.py", "**/*.py" and "src/**/*.py" interchangeably and mean the
    same thing. Handle all three here -- cheaper than making the executor repair the
    pattern after a round-trip that found nothing."""
    normalised = pattern.replace(os.sep, "/")

    if normalised.startswith("**/") and "/" not in normalised[3:]:
        # "**/*.py" with no further separator -> match the BASENAME.
        tail = normalised[3:]
        return lambda rel: fnmatch.fnmatch(rel.rsplit("/", 1)[-1], tail)

    if "/" not in normalised:
        # A bare "*.py" means "anywhere", not "in the root only".
        return lambda rel: fnmatch.fnmatch(rel.rsplit("/", 1)[-1], normalised)

    # Otherwise match the relative path both with and without a leading "**/".
    bare = normalised[3:] if normalised.startswith("**/") else normalised
    return lambda rel: fnmatch.fnmatch(rel, bare) or fnmatch.fnmatch(rel, f"**/{bare}")


# ==============================================================================
# 2 · The walk  --  why not Path.glob
# ==============================================================================

def _cursor_encode(mtime_ns: int, rel: str) -> str:
    return base64.urlsafe_b64encode(f"{mtime_ns}:{rel}".encode()).decode()


def _cursor_decode(raw: str) -> tuple[int, str]:
    try:
        mtime, _, rel = base64.urlsafe_b64decode(raw.encode()).decode().partition(":")
        return int(mtime), rel
    except (ValueError, UnicodeDecodeError):
        raise ToolSemanticError(
            "cursor is not a cursor this tool issued.",
            remedy="Omit cursor to start from the first page.",
        ) from None


def _walk_and_match(root: Path, pattern: str, limit: int,
                    after: tuple[int, str] | None,
                    cancel: threading.Event) -> tuple[list[tuple[int, str]], bool]:
    """Runs in a worker thread. Returns (candidates, hit_work_cap).

    Path.glob CANNOT prune -- there is no hook to skip a directory mid-walk, so `**`
    descends into .venv no matter what you do afterwards. Hence os.walk by hand.
    """
    match = _matcher(pattern)
    budget = limit * CANDIDATE_FACTOR
    found: list[tuple[int, str]] = []
    hit_cap = False

    # followlinks=False is the default -- KEEP it. A symlinked directory pointing
    # outside the root would otherwise walk straight out of the workspace.
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        # The SLICE ASSIGNMENT is what makes this work: os.walk reads this list back to
        # decide where to descend. Rebinding `dirnames = [...]` silently does nothing,
        # and that is a fun afternoon to lose.
        dirnames[:] = [d for d in dirnames if not prunable(d)]

        # A thread cannot be killed, so the walk POLLS between directories. Without
        # this a cancelled turn leaves a thread walking a monorepo to completion,
        # holding a worker nobody is waiting for any more.
        if cancel.is_set():
            break

        for name in filenames:
            full = Path(dirpath) / name
            rel = relativise(full, root).replace(os.sep, "/")
            if not match(rel):
                continue
            # Resolve before emitting so a symlinked FILE cannot leak a path outside
            # the root either.
            try:
                if not full.resolve().is_relative_to(root):
                    continue
                mtime_ns = full.stat().st_mtime_ns
            except OSError:
                continue          # vanished mid-walk; not an error, just not a result
            if after is not None and (-mtime_ns, rel) <= (-after[0], after[1]):
                continue
            found.append((mtime_ns, rel))
            if len(found) >= budget:
                hit_cap = True
                return found, hit_cap

    return found, hit_cap


# ==============================================================================
# 3 · Threading + cancellation
# ==============================================================================

class GlobTool(BaseTool[GlobArgs, GlobOutput]):

    spec = ToolSpec[GlobArgs, GlobOutput](
        name="glob",
        version="1.0.0",
        description=(
            "Find files by NAME pattern, newest first. Supports '*.py', '**/*.py' and "
            "'src/**/*.py'. Results are capped -- when truncated is true, pass the "
            "returned cursor to get the next page. Use grep to search file CONTENT."
        ),
        input_model=GlobArgs,
        output_adapter=TypeAdapter(GlobOutput),
        category=ToolCategory.SEARCH,
        side_effect=SideEffect.NONE,
        risk_level=RiskLevel.LOW,
        capabilities=frozenset({"fs.search", "fs.read"}),
        default_permission=Decision.ALLOW,
        concurrency=ConcurrencyClass.READ_PARALLEL,
        resource_keys=lambda args: (f"fs:{args.path}:read",),
        timeout=TimeoutPolicy(default_s=15.0, max_s=30.0),
        interrupt_behavior=InterruptBehavior.CANCEL,
        idempotency=Idempotency.IDEMPOTENT,
        max_inline_result_bytes=64 * 1024,
        aliases=("Glob",),
    )

    async def validate_semantics(self, args: GlobArgs, ctx: ToolRuntimeContext) -> None:
        root = confine(ctx, args.path)
        if not root.is_dir():
            raise ToolSemanticError(
                f"{relativise(root, ctx.workspace_root)} is not a directory.",
                remedy="path is the search ROOT -- pass a directory, not a file.",
            )

    def human_summary(self, args: GlobArgs) -> str:
        where = "" if args.path == "." else f" under {args.path}"
        return f"Find files matching {args.pattern}{where}"

    async def execute(self, args: GlobArgs, ctx: ToolRuntimeContext) -> GlobOutput:
        root = confine(ctx, args.path)
        after = _cursor_decode(args.cursor) if args.cursor else None
        started = time.perf_counter()

        # os.walk is blocking CPU + syscall work. In an async def it stalls the event
        # loop, and once the executor batches tools one glob freezes every sibling call.
        # Under asyncio debug mode no callback may exceed 50 ms.
        # abandon_on_cancel=True returns control to the loop immediately on cancel;
        # the event is what actually stops the orphaned thread a moment later.
        cancel = threading.Event()
        try:
            candidates, hit_cap = await anyio.to_thread.run_sync(
                _walk_and_match, root, args.pattern, args.limit, after, cancel,
                abandon_on_cancel=True,
            )
        except BaseException:
            cancel.set()
            raise

        # RECENCY ORDER: newest mtime first. Results are truncated, so the ordering
        # decides what SURVIVES the cut -- and the recently-touched file is almost
        # always the one being asked about.
        #
        # DETERMINISM: mtime ties are common -- a checkout writes a whole tree in the
        # same second. Break ties on the relative path, or two identical calls return
        # two different orders and pagination silently skips and repeats files.
        candidates.sort(key=lambda pair: (-pair[0], pair[1]))

        page = candidates[:args.limit]
        truncated = hit_cap or len(candidates) > args.limit
        cursor = _cursor_encode(*page[-1]) if truncated and page else None

        return GlobOutput(
            duration_ms=(time.perf_counter() - started) * 1000.0,
            num_files=len(page),
            filenames=[rel for _, rel in page],
            truncated=truncated,
            cursor=cursor,
        )


# ==============================================================================
# Gate  ->  tests/agent/test_tools_search.py
# ==============================================================================

# NOTE ->> glob("**/*.py") on this repo: p95 <= 120 ms, and ZERO .venv results.
# NOTE ->> newest-first, with path as a deterministic tie-break.
# NOTE ->> two identical calls return identical ordering.
# NOTE ->> a symlinked directory pointing outside the root is not followed.
# NOTE ->> refuses a search root outside the project.
# NOTE ->> truncated=True is set whenever the cap was hit.
# NOTE ->> cancellation mid-traversal actually stops the walk.
# NOTE ->> the event loop is never blocked > 50 ms under asyncio debug mode.
