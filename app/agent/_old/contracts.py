"""Core agent contracts -- the Python realisation of `docs/tools.md`.

The conceptual model from `docs/tools.md` is preserved exactly:

    tool definition -> validate arguments -> permission decision -> execute -> ToolResult

What differs is the executor. `docs/tools.md` sketches a hand-rolled
`execute_tool_call`; LangGraph already ships that loop as `ToolNode`, with parallel
dispatch, error handling, state injection and streaming solved. So we keep the part
LangGraph does NOT give us -- the `ToolSpec` metadata -- and let `ToolNode` execute.

`ToolSpec.read_only` and `ToolSpec.concurrency_safe` are load-bearing, exactly as in
`docs/tools.md`: they decide what may run in the same parallel batch, what may be
cached, and what needs a permission prompt.

IMPORT COST RULE: this module is imported before the CLI prints its first frame.
It must never import langchain, langgraph, torch or anything else expensive.
Measured: `import langgraph.graph` alone costs ~1800 ms.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.agent.runtime.cold_lane import ColdLane


# ──────────────────────────────────────────────────────────────────────────────
# Results
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class ToolResult:
    """What the model sees after a tool finishes.

    `content` is what goes into the conversation. Keep it terse: in an agent loop,
    every token of tool output is re-sent on every subsequent turn, so verbose tool
    results are a compounding latency cost, not just a one-off one.
    """

    content: str
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    #: Bytes/lines truncated from `content`, if any. Surfaced so the model knows
    #: it is looking at a partial view and can ask for more.
    truncated: int = 0

    @classmethod
    def error(cls, message: str, **meta: Any) -> "ToolResult":
        return cls(content=message, is_error=True, metadata=meta)

    @classmethod
    def ok(cls, content: str, **meta: Any) -> "ToolResult":
        return cls(content=content, metadata=meta)


# ──────────────────────────────────────────────────────────────────────────────
# Permissions
# ──────────────────────────────────────────────────────────────────────────────


class PermissionMode(StrEnum):
    """Session-wide posture. Mirrors the modes in `docs/tools.md`'s PermissionContext."""

    DEFAULT = "default"
    """Read-only tools auto-allowed; everything else prompts."""

    ACCEPT_EDITS = "accept_edits"
    """File edits auto-allowed; shell and destructive operations still prompt."""

    PLAN = "plan"
    """Nothing may mutate. Read-only tools only -- the agent researches and proposes."""

    BYPASS = "bypass"
    """Everything auto-allowed. For non-interactive/CI use only."""


class RiskLevel(StrEnum):
    """How much damage a tool can do. Drives the permission decision."""

    SAFE = "safe"
    """Read-only, confined to the project. Auto-allowed."""

    LOW = "low"
    """Writes inside the project, easily reverted (creating a new file)."""

    MEDIUM = "medium"
    """Modifies existing project state (editing a tracked file)."""

    HIGH = "high"
    """Destructive, irreversible, or escapes the project (delete, shell, network)."""


class Decision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass(slots=True)
class PermissionResult:
    decision: Decision
    reason: str = ""
    #: Rule that produced this decision, for `--explain` and audit output.
    rule: str = ""

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW


@dataclass
class PermissionContext:
    """Policy state, kept separate from UI. The UI asks the user; this decides.

    Patterns are matched against a tool-scoped string produced by
    `Tool.permission_key(args)` -- e.g. `bash(git push)` or `write_file(src/main.py)`
    -- so a rule can allow `bash(git *)` without allowing all shell access.
    """

    mode: PermissionMode = PermissionMode.DEFAULT
    always_allow: set[str] = field(default_factory=set)
    always_deny: set[str] = field(default_factory=set)

    #: Approvals granted for this session only, never persisted.
    session_allow: set[str] = field(default_factory=set)

    def remember_allow(self, key: str, *, persist: bool = False) -> None:
        (self.always_allow if persist else self.session_allow).add(key)


# ──────────────────────────────────────────────────────────────────────────────
# Tool metadata
# ──────────────────────────────────────────────────────────────────────────────


class ToolCategory(StrEnum):
    FILESYSTEM = "filesystem"
    SEARCH = "search"
    EXECUTION = "execution"
    TASK = "task"
    WEB = "web"
    UTILITY = "utility"


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Metadata LangGraph does not carry for us.

    `budget_ms` is enforced, not documentation: `tests/agent/test_tool_budgets.py`
    asserts against it so a tool that gets slower fails CI rather than quietly
    eroding the latency contract.
    """

    name: str
    category: ToolCategory
    risk: RiskLevel
    read_only: bool
    concurrency_safe: bool
    timeout_s: float
    budget_ms: int
    description: str = ""

    #: None disables caching. Only read-only tools may be cached.
    cache_ttl_s: int | None = None

    #: Tools not available in PLAN mode (anything that mutates).
    plan_mode_safe: bool = True

    def __post_init__(self) -> None:
        if self.read_only and self.risk not in (RiskLevel.SAFE,):
            raise ValueError(f"{self.name}: read_only tools must be SAFE, got {self.risk}")
        if self.cache_ttl_s is not None and not self.read_only:
            raise ValueError(f"{self.name}: only read_only tools may be cached")
        if not self.read_only and self.plan_mode_safe:
            raise ValueError(f"{self.name}: mutating tools cannot be plan_mode_safe")
        if self.timeout_s <= 0:
            raise ValueError(f"{self.name}: timeout_s must be positive")


# ──────────────────────────────────────────────────────────────────────────────
# Execution context
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class AgentContext:
    """Dependencies supplied by the app rather than imported globally.

    This is `docs/tools.md`'s `ToolContext`. At runtime it reaches tools through
    LangGraph's `ToolRuntime.context`, so tools never import global state.
    """

    cwd: Path
    permission: PermissionContext = field(default_factory=PermissionContext)

    session_id: str = ""
    request_id: str = ""

    provider: str = "ollama"
    model: str = ""

    #: Monotonic deadline. A tool that cannot finish in time returns a partial
    #: result rather than blowing the turn budget.
    deadline_at: float = 0.0
    started_at: float = field(default_factory=time.monotonic)

    in_progress_tool_ids: set[str] = field(default_factory=set)
    confirmed_tool_calls: set[str] = field(default_factory=set)

    cold: "ColdLane | None" = None

    #: Optional UI callbacks. Only supplied by the interactive terminal app; a
    #: batch/SDK invocation leaves these None and has no dependency on terminal state.
    on_progress: Callable[[str], None] | None = None
    on_permission_request: Callable[..., Any] | None = None

    extras: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.cwd = Path(self.cwd).resolve()

    # -- deadlines ------------------------------------------------------------

    def remaining_s(self) -> float:
        if not self.deadline_at:
            return float("inf")
        return max(0.0, self.deadline_at - time.monotonic())

    def elapsed_ms(self) -> float:
        return (time.monotonic() - self.started_at) * 1000.0

    def budget_for(self, spec: ToolSpec) -> float:
        """The tool's own ceiling, clipped by what is left of the turn."""
        return min(spec.timeout_s, self.remaining_s())

    def confirmed(self, tool_call_id: str) -> bool:
        return tool_call_id in self.confirmed_tool_calls

    # -- path safety ----------------------------------------------------------

    def resolve_in_project(self, raw: str) -> Path:
        """Resolve `raw` under cwd, refusing anything that escapes the project.

        Raises ValueError on traversal. Every filesystem tool must route through
        this -- it is the single chokepoint for `../../etc/passwd`.

        Uses `Path.resolve()` first so symlinks cannot be used to step outside.
        """
        root = self.cwd
        candidate = Path(raw)
        resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
        if resolved != root and root not in resolved.parents:
            raise ValueError(f"Path escapes the project root: {raw}")
        return resolved
