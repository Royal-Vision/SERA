"""Grep -- P0 · fs.search. Step 5 · Phase 04 + Tool Catalog §P0.

grep("def handle_login") -> app/auth/routes.py:42, then one targeted read. One
round-trip where a read-only agent would have spent four. Against the Phase 00
budget of roundtrips <= 4, search is not a convenience -- it is most of the budget.

Default target decision: ALLOW inside the approved workspace.
"""

# Shares PRUNE_DIRS, the threading rule and the cancellation rule with glob.py --
# read those notes first.

import fnmatch
import os
import re
import shutil
import subprocess
import threading
import time
from enum import StrEnum
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

# CONFLICT ->> RESOLVED: default 100, ceiling 250.
# CONFLICT ->> Phase 04 said limit=100 (le=300), the catalog said 250. The DEFAULT is a
# CONFLICT ->> token bill paid on every call; the CEILING is paid only when asked for.
# CONFLICT ->> So keep the cheap default and raise the ceiling to the catalog's number.
DEFAULT_LIMIT = 100
MAX_LIMIT = 250

# (c) SKIP HUGE FILES in the Python fallback -- one 200 MB log eats the whole timeout.
MAX_FILE_BYTES = 4 * 1024 * 1024

# (e) CATASTROPHIC BACKTRACKING. `(a+)+$` against a long line hangs the regex engine
# inside the worker thread, where timeout_s cannot reach it -- Python's re has no step
# limit. rg (Rust regex) is linear-time and immune; the PYTHON FALLBACK is the exposed
# one, so bound line length before matching.
MAX_LINE_BYTES = 8 * 1024


class OutputMode(StrEnum):
    """Making the model choose is what keeps a broad search from costing a narrow one."""

    CONTENT = "content"
    """file:line:text -- usually what is wanted."""
    FILES_WITH_MATCHES = "files_with_matches"
    """Paths only. "Which files mention X" -- far cheaper."""
    COUNT = "count"
    """Per-file counts. "How widespread is this"."""


# ==============================================================================
# 2 · Input
# ==============================================================================

class GrepArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    pattern: str = Field(min_length=1, description="Regular expression to search for.")
    path: str = Field(default=".", description="Search root; defaults to the workspace.")
    glob: str | None = Field(default=None, description='Only files matching, e.g. "*.py".')
    output_mode: OutputMode = OutputMode.CONTENT
    case_insensitive: bool = False
    multiline: bool = False
    before_context: int = Field(default=0, ge=0, le=10)
    after_context: int = Field(default=0, ge=0, le=10)

    # head_limit=0 is PRIVILEGED, not ordinary input: it means unbounded. ge=1 keeps it
    # out of the MODEL schema entirely -- otherwise it is the one call that dumps a
    # whole repo into the context window. Internal callers construct GrepArgs directly.
    head_limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)
    offset: int = Field(default=0, ge=0)


class GrepMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    line_number: int
    line: str


class GrepOutput(BaseModel):
    """Echo the pagination back -- the model cannot ask for page 2 if it was never
    told which page it got."""

    model_config = ConfigDict(extra="forbid")

    mode: OutputMode
    duration_ms: float
    num_files: int
    filenames: list[str]
    matches: list[GrepMatch] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    total_matches: int = 0
    offset: int = 0
    head_limit: int = DEFAULT_LIMIT
    truncated: bool = False


# ==============================================================================
# 1 · Two backends, and the fallback is NOT optional
# ==============================================================================

_RG_PATH: str | None = None
_RG_LOOKED = False


def _ripgrep() -> str | None:
    """Detect ONCE, cache, degrade silently. A CLI that only works if the user happens
    to have `rg` installed is broken for most users.

    A separate _RG_LOOKED flag rather than a None sentinel: None is the ANSWER when rg
    is absent, so folding "absent" and "not yet checked" into one value re-runs
    shutil.which on every call in exactly the case where it already failed.
    """
    global _RG_PATH, _RG_LOOKED
    if not _RG_LOOKED:
        _RG_PATH = shutil.which("rg")
        _RG_LOOKED = True
    return _RG_PATH


def _rg_argv(rg: str, args: GrepArgs, root: Path) -> list[str]:
    """ARGUMENT VECTOR, NEVER A SHELL STRING. This is the security item in this file.

    The pattern is attacker-influenced text from a model. Interpolated into a shell
    string, a pattern containing $(...) or ; is command execution -- from a tool
    declared read-only, SAFE, and auto-allowed in DEFAULT mode. shell=False plus `-e`
    so a pattern starting with "-" is a pattern and not a flag.
    """
    # ONE output shape for every mode: path:line:text. rg has cheaper native
    # --files-with-matches and --count flags, but each returns a DIFFERENT shape, and
    # the gate requires the two backends to agree byte for byte. Deriving all three
    # modes from one normalised stream is what makes that true by construction rather
    # than by two parsers that have to be kept in step.
    argv = [rg, "--no-messages", "--no-config", "-e", args.pattern,
            "--line-number", "--no-heading", "--with-filename"]

    if args.output_mode is OutputMode.CONTENT:
        if args.before_context:
            argv += ["-B", str(args.before_context)]
        if args.after_context:
            argv += ["-A", str(args.after_context)]

    if args.case_insensitive:
        argv.append("-i")
    if args.multiline:
        argv += ["--multiline", "--multiline-dotall"]
    if args.glob:
        argv += ["--glob", args.glob]
    for pruned in sorted(PRUNED_GLOBS):
        argv += ["--glob", pruned]

    argv.append(".")
    return argv


PRUNED_GLOBS = frozenset(
    f"!**/{name}/**" for name in
    (".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache",
     ".pytest_cache", ".ruff_cache", "dist", "build", ".next", ".tox", "site-packages")
)


def _run_rg(rg: str, args: GrepArgs, root: Path, timeout_s: float) -> list[str] | None:
    """None means "real failure, fall back". subprocess.run BLOCKS -- caller threads it."""
    try:
        proc = subprocess.run(
            _rg_argv(rg, args, root), shell=False, capture_output=True,
            cwd=root, text=True, errors="replace", timeout=timeout_s,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    # (a) `rg` EXITS 1 FOR "NO MATCHES". A valid empty result, not a failure. Treating
    # exit 1 as an error makes the tool report failure on every successful no-match
    # search -- which is most of them.
    if proc.returncode not in (0, 1):
        return None
    return proc.stdout.splitlines()


# ==============================================================================
# Python fallback
# ==============================================================================

def _py_search(args: GrepArgs, root: Path, cancel: threading.Event) -> list[str]:
    flags = re.IGNORECASE if args.case_insensitive else 0
    if args.multiline:
        flags |= re.DOTALL | re.MULTILINE
    rx = re.compile(args.pattern, flags)
    glob_rx = args.glob
    lines: list[str] = []

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if not prunable(d)]
        if cancel.is_set():
            break

        for name in filenames:
            if glob_rx is not None and not fnmatch.fnmatch(name, glob_rx):
                continue
            full = Path(dirpath) / name
            try:
                if full.stat().st_size > MAX_FILE_BYTES:
                    continue
                # (d) errors="ignore": a binary file that slipped past the glob filter
                # should be skipped, not crash the scan.
                with full.open("r", encoding="utf-8", errors="ignore") as handle:
                    for number, line in enumerate(handle, start=1):
                        if len(line) > MAX_LINE_BYTES:
                            continue          # see MAX_LINE_BYTES -- backtracking guard
                        if rx.search(line):
                            rel = relativise(full, root).replace(os.sep, "/")
                            lines.append(f"{rel}:{number}:{line.rstrip()}")
            except OSError:
                continue
    return lines


# ==============================================================================
# 3 · Output modes
# ==============================================================================

def _shape(raw: list[str], args: GrepArgs, root: Path, duration_ms: float) -> GrepOutput:
    hits = _parse(raw, root)

    if args.output_mode is OutputMode.FILES_WITH_MATCHES:
        files = sorted({hit.path for hit in hits})
        page = files[args.offset:args.offset + args.head_limit]
        return GrepOutput(
            mode=args.output_mode, duration_ms=duration_ms, num_files=len(page),
            filenames=page, total_matches=len(files), offset=args.offset,
            head_limit=args.head_limit, truncated=len(files) > args.offset + len(page),
        )

    if args.output_mode is OutputMode.COUNT:
        counts: dict[str, int] = {}
        for hit in hits:
            counts[hit.path] = counts.get(hit.path, 0) + 1
        # Widest first, path as the deterministic tie-break -- same reason as glob.
        ordered = dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
        page_c = dict(list(ordered.items())[args.offset:args.offset + args.head_limit])
        return GrepOutput(
            mode=args.output_mode, duration_ms=duration_ms, num_files=len(page_c),
            filenames=list(page_c), counts=page_c, total_matches=sum(ordered.values()),
            offset=args.offset, head_limit=args.head_limit,
            truncated=len(ordered) > args.offset + len(page_c),
        )

    page_m = hits[args.offset:args.offset + args.head_limit]
    return GrepOutput(
        mode=args.output_mode, duration_ms=duration_ms,
        num_files=len({m.path for m in page_m}),
        filenames=sorted({m.path for m in page_m}), matches=page_m,
        total_matches=len(hits), offset=args.offset, head_limit=args.head_limit,
        truncated=len(hits) > args.offset + len(page_m),
    )


_HIT = re.compile(r"^(?P<path>.*?):(?P<line>\d+):(?P<text>.*)$")


def _parse(raw: list[str], root: Path) -> list[GrepMatch]:
    hits: list[GrepMatch] = []
    for line in raw:
        found = _HIT.match(line)
        if found is None:
            continue          # rg context lines use path-line-text; not a match itself
        hits.append(GrepMatch(
            path=_rel(found["path"], root),
            line_number=int(found["line"]),
            line=found["text"],
        ))
    return hits


def _rel(raw: str, root: Path) -> str:
    return relativise(Path(raw), root).replace(os.sep, "/")


class GrepTool(BaseTool[GrepArgs, GrepOutput]):

    spec = ToolSpec[GrepArgs, GrepOutput](
        name="grep",
        version="1.0.0",
        description=(
            "Search file CONTENT by regular expression. output_mode picks the cost: "
            "'content' returns file:line:text, 'files_with_matches' returns paths only "
            "and is far cheaper, 'count' returns per-file totals. Use glob to search "
            "by file NAME instead."
        ),
        input_model=GrepArgs,
        output_adapter=TypeAdapter(GrepOutput),
        category=ToolCategory.SEARCH,
        side_effect=SideEffect.NONE,
        risk_level=RiskLevel.LOW,
        capabilities=frozenset({"fs.search", "fs.read"}),
        default_permission=Decision.ALLOW,
        concurrency=ConcurrencyClass.READ_PARALLEL,
        # 6 · REGEX CONTENT DOES NOT WIDEN FILESYSTEM SCOPE. The pattern chooses what
        # matches, never where to look -- only `path` does that, and only after
        # containment. So the lock key is the search ROOT and never the pattern.
        resource_keys=lambda args: (f"fs:{args.path}:read",),
        timeout=TimeoutPolicy(default_s=30.0, max_s=60.0),
        interrupt_behavior=InterruptBehavior.CANCEL,
        idempotency=Idempotency.IDEMPOTENT,
        max_inline_result_bytes=128 * 1024,
        aliases=("Grep",),
    )

    # ==========================================================================
    # 5 · Validate the regex BEFORE running it
    # ==========================================================================

    async def validate_semantics(self, args: GrepArgs, ctx: ToolRuntimeContext) -> None:
        root = confine(ctx, args.path)
        if not root.is_dir():
            raise ToolSemanticError(
                f"{relativise(root, ctx.workspace_root)} is not a directory.",
                remedy="path is the search ROOT -- pass a directory, not a file.",
            )
        try:
            re.compile(args.pattern)
        except re.error as exc:
            # VERBATIM. "unbalanced parenthesis at position 12" is exactly the
            # actionable error we want -- an error message is a prompt, and this one is
            # already written for us.
            raise ToolSemanticError(
                f"invalid regular expression: {exc}",
                remedy="Fix the pattern and search again.",
                pattern=args.pattern,
            ) from None

    def human_summary(self, args: GrepArgs) -> str:
        where = "" if args.path == "." else f" under {args.path}"
        return f"Search for /{args.pattern}/{where}"

    async def execute(self, args: GrepArgs, ctx: ToolRuntimeContext) -> GrepOutput:
        root = confine(ctx, args.path)
        started = time.perf_counter()
        budget = ctx.turn.budget_for(self.spec)

        rg = _ripgrep()
        raw: list[str] | None = None
        backend = "python"

        if rg is not None:
            raw = await anyio.to_thread.run_sync(_run_rg, rg, args, root, budget)
            if raw is not None:
                backend = "ripgrep"

        if raw is None:
            cancel = threading.Event()
            try:
                raw = await anyio.to_thread.run_sync(
                    _py_search, args, root, cancel, abandon_on_cancel=True,
                )
            except BaseException:
                cancel.set()
                raise

        out = _shape(raw, args, root, (time.perf_counter() - started) * 1000.0)
        # Report the backend: when the two disagree you want to learn it from a log,
        # not a bug report. The executor lifts this into ToolResult.metadata.
        ctx.turn.extras["grep_backend"] = backend
        return out


# ==============================================================================
# Gate  ->  tests/agent/test_tools_search.py
# ==============================================================================

# NOTE ->> grep p95 <= 250 ms on this repo.
# NOTE ->> rg and Python backends return IDENTICAL results on a fixture set.
# NOTE ->> rg absent -> falls back silently, metadata["backend"] == "python".
# NOTE ->> no match -> a clear empty result, NOT an error.
# NOTE ->> invalid regex -> the re.error message, verbatim.
# NOTE ->> a pattern containing `;` / `$(...)` / a leading `-` executes nothing and is
# NOTE ->>   treated as a literal pattern.
# NOTE ->> a catastrophic-backtracking pattern does not hang the fallback past timeout_s.
# NOTE ->> head_limit=0 from the model schema is rejected.
# NOTE ->> refuses a search root outside the project.

# NOTE ->> (b) OutputMode is a StrEnum, so pydantic renders it as a $ref into $defs, NOT
# NOTE ->> an inline enum. The executor's argument coercion looks for an inline enum and
# NOTE ->> will miss "CONTENT" -> "content". This is the tool that surfaces it -- the fix
# NOTE ->> belongs in the executor, not here.
