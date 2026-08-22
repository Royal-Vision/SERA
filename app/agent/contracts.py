"""Core agent contracts. The bottom of the stack -- everything imports from here."""

# NOTE ->> Import discipline starts here: stdlib + pydantic ONLY. No langchain/langgraph/torch.
# NOTE ->> No `from __future__ import annotations` -- 3.14 defers annotations by default (PEP 649).
# NOTE ->> You will need: time, dataclass/field, StrEnum, Path, Any/Callable/TYPE_CHECKING.
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING


# ==============================================================================
# 1 · Results
# ==============================================================================

# NOTE ->> ToolResult: @dataclass(slots=True). Fields: content, is_error, metadata, truncated.
# NOTE ->> Keep `content` terse -- it is re-sent to the model on EVERY later turn of the loop.
# NOTE ->> `truncated` = how much you cut. The model must know when its view is partial.
# NOTE ->> Add classmethods .ok(content, **meta) and .error(message, **meta) to keep call sites short.
@dataclass(slots=True)
class ToolResult:
    """What the model sees after a tool finishes.

    Every terminal state of a tool becomes one of these -- success, failure,
    timeout, refusal. Nothing escapes as an exception.
    """

    content: str
    """What goes into the conversation. Keep it terse: in an agent loop every
    token of tool output is re-sent on every later turn, so a verbose result is
    a compounding cost, not a one-off one."""

    is_error: bool = False
    """True marks the ToolMessage as an error so the model treats it as feedback."""

    metadata: dict[str, Any] = field(default_factory=dict)
    """Out-of-band facts for the engine and logs -- never shown to the model.
    Must be default_factory: a bare {} would be shared by every instance."""

    truncated: int = 0
    """How much was cut. An int, not a bool -- the model needs to know how much
    it is missing to decide whether to ask for the rest."""

    @classmethod
    def ok(cls, content: str, **meta: Any) -> "ToolResult":
        return cls(content=content, metadata=meta)

    @classmethod
    def error(cls, message: str, **meta: Any) -> "ToolResult":
        return cls(content=message, is_error=True, metadata=meta)

# ==============================================================================
# 2 · Enums   (all StrEnum -- these get logged and serialised as-is)
# ==============================================================================

# NOTE ->> PermissionMode: DEFAULT, ACCEPT_EDITS, PLAN, BYPASS. Session-wide posture.
class PermissionMode(StrEnum):
    """Session-wide posture: how much the agent may do without asking.

    Chosen once per session by the USER, never by the agent. The policy in
    base.py reads it on every single tool call.
    """

    DEFAULT = "DEFAULT"
    """Read-only tools auto-allowed; every mutation prompts.

    Use for: normal interactive work. This is the only mode that may ever be
    assumed -- the other three must each be asked for deliberately.
    """

    ACCEPT_EDITS = "ACCEPT_EDITS"
    """File edits auto-allowed; bash and anything HIGH risk still prompts.

    Use for: a long refactor across many files, where approving each edit is
    pure friction because you already agreed the agent may rewrite the project.
    The HIGH-risk carve-out is what keeps this from becoming BYPASS by accident.
    """

    PLAN = "PLAN"
    """Nothing may mutate. Read-only tools only -- research and propose.

    Use for: "show me what you would do before you touch anything."
    Two properties make this a wall rather than a preference: mutating tools are
    not merely denied but NOT OFFERED (so the model never wastes a turn trying
    one), and it is checked BEFORE allow-lists, so no approval granted earlier
    in the session can override it.
    """

    BYPASS = "BYPASS"
    """Everything auto-allowed -- except always_deny, which still wins.

    Use for: CI and non-interactive runs, where there is nobody to answer a
    prompt and ASK would just hang. Never infer it from the absence of a
    terminal -- a missing TTY means deny, not bypass. That the deny-list still
    applies here is exactly what makes this mode safe enough to exist.
    """


# NOTE ->> RiskLevel: SAFE, LOW, MEDIUM, HIGH. How much damage the tool can do.
class RiskLevel(StrEnum):
    """How much damage a tool can do. Drives the permission decision."""

    SAFE = "SAFE"
    """read_file, glob, grep -- read-only and confined to the project."""

    LOW = "LOW"
    """write_file to a NEW path -- trivially reverted, nothing was lost."""

    MEDIUM = "MEDIUM"
    """edit_file on an existing tracked file -- changes state that already mattered."""

    HIGH = "HIGH"
    """bash, delete, network -- irreversible, or escapes the project.
    The one level ACCEPT_EDITS refuses to auto-approve."""


# NOTE ->> Decision: ALLOW, DENY, ASK. Three outcomes -- this is exactly why a bool is not enough.
class Decision(StrEnum):
    """The three outcomes of a permission check -- which is why it cannot be a bool."""

    ALLOW = "ALLOW"
    """Run it now, no prompt."""

    DENY = "DENY"
    """Never run. Returns a ToolMessage, not an exception: the model must SEE the
    refusal and adapt. Killing the turn would lose all in-flight work."""

    ASK = "ASK"
    """Suspend and ask the user. The policy never prompts itself -- the caller
    turns this into a terminal prompt, a graph interrupt, or an automatic deny."""

# NOTE ->> ToolCategory: FILESYSTEM, SEARCH, EXECUTION, TASK, WEB, UTILITY.
class ToolCategory(StrEnum):
    """What kind of work a tool does. Grouping for docs, metrics and tool listings."""

    FILESYSTEM = "FILESYSTEM"
    """read_file, write_file, edit_file -- touch files inside the project root."""

    SEARCH = "SEARCH"
    """glob, grep -- find things without reading everything.
    The cheapest lever on how many turns a task takes."""

    EXECUTION = "EXECUTION"
    """bash. The only category that cannot be made safe by structure, so it is
    the one that genuinely needs a permission decision."""

    TASK = "TASK"
    """Spawn a subagent that works in its own context window. Phase 13, deferred."""

    WEB = "WEB"
    """web_fetch, web_search. None shipped by default -- no network tool means
    prompt injection has no channel to exfiltrate anything through."""

    UTILITY = "UTILITY"
    """Everything else -- todo list, formatting, diagnostics."""

# ==============================================================================
# 3 · Permission data   (data only -- PermissionPolicy lives in base.py, Step 2)
# ==============================================================================

# NOTE ->> `rule` names WHICH branch decided, so an audit log / --explain can justify itself.
@dataclass(slots=True)  # slots: no per-instance __dict__ -- smaller, and typos raise instead of sticking
class PermissionResult:
    """The outcome of one permission check. Pure data -- the policy lives in base.py."""

    decision: Decision
    """ALLOW / DENY / ASK."""

    reason: str = ""
    """Prose, for a human or the model to read. Reworded freely -- never key on it."""

    rule: str = ""
    """Stable token naming the branch that decided: "always_deny", "plan_mode",
    "read_only", "bypass", "always_allow", "session", "accept_edits", "default".
    This is what tests assert on and what metrics group by."""

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW



# NOTE ->> PermissionContext: @dataclass, MUTABLE. mode, always_allow, always_deny, session_allow.
# NOTE ->> Three sets, not one: always_* persist to config, session_allow dies with the process.
# NOTE ->> remember_allow(key, persist=False) -> route into the right set. One line.
@dataclass(slots=True, frozen=False)
class PermissionContext:
    """Mutable state for the permission policy. One instance per session.

    The policy reads this on every tool call, and may mutate it when the user
    approves a prompt. It is never imported globally -- the engine passes it in.
    """

    mode: PermissionMode
    """Session-wide posture: how much the agent may do without asking."""

    always_allow: set[str] = field(default_factory=set)
    """Persisted to config -- a user-approved allow-list of tool IDs."""

    always_deny: set[str] = field(default_factory=set)
    """Persisted to config -- a user-approved deny-list of tool IDs."""

    session_allow: set[str] = field(default_factory=set)
    """Dies with the process -- a user-approved allow-list of tool IDs."""

    def remember_allow(self, key: str, persist: bool = False) -> None:
        """Route one approval into the right set. persist=True outlives the process.

        Default False on purpose: an approval is temporary unless the user says
        otherwise, so a misclick costs one session, not every session after it.
        """
        (self.always_allow if persist else self.session_allow).add(key)

# ==============================================================================
# 4 · ToolSpec
# ==============================================================================

# NOTE ->> @dataclass(frozen=True, slots=True). Built once per tool class, never mutated.
# NOTE ->> Fields: name, category, risk, read_only, concurrency_safe, timeout_s, budget_ms,
# NOTE ->>         description="", cache_ttl_s=None, plan_mode_safe=True.
# NOTE ->> read_only + concurrency_safe are load-bearing: they decide parallelism, caching, prompting.
# NOTE ->> budget_ms is a latency assertion, not a comment -- a test will fail when a tool slows down.
# NOTE ->> __post_init__ must REJECT incoherent specs at import time. An impossible tool
# NOTE ->> should be un-constructible, not a runtime surprise. Four rules:
# NOTE ->> (a) read_only  =>  risk is SAFE
# NOTE ->> (b) cache_ttl_s is not None  =>  read_only
# NOTE ->> (c) not read_only  =>  not plan_mode_safe
# NOTE ->> (d) timeout_s > 0
@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Declared facts about a tool, read by the engine INSTEAD of asking the tool.

    frozen: built once per tool class, so it can be shared and trusted.
    """

    name: str
    """Identity the model calls and logs group by. str, not an enum -- the tool
    set is open, plugins register names this file never sees."""

    category: ToolCategory
    """Grouping for listings and metrics. Enum: closed set, so a typo raises here
    instead of quietly creating a category of one."""

    risk: RiskLevel
    """Top input to the permission decision. Enum for the same reason, and because
    tests and metrics key on these exact four tokens."""

    read_only: bool
    """Changes nothing -- no file, no process, no remote state. Load-bearing three
    times: DEFAULT auto-allows it, only it may be cached, only it survives PLAN."""

    concurrency_safe: bool
    """Two calls may overlap. Decides fan-out vs serial -- a turn that takes a
    second instead of ten."""

    timeout_s: float
    """Hard ceiling on one call. Seconds because it is compared against the turn
    deadline, which is also seconds (time.monotonic)."""

    budget_ms: float
    """Asserted latency, not a comment -- a test fails when the tool slows past it.
    Milliseconds because tools are sub-second; seconds would be all zeroes."""

    description: str = ""
    """Model-facing prose. Defaults empty because it is re-sent every turn: pay
    tokens only where the model actually needs steering."""

    cache_ttl_s: float | None = None
    """Seconds a result stays reusable. None rather than 0 -- "never cache" and
    "cached, already expired" are different states."""

    plan_mode_safe: bool = True
    """May be OFFERED in PLAN mode -- withheld from the listing, not merely denied.
    Defaults True for the read-only majority; __post_init__ rejects it on mutators."""

    def __post_init__(self) -> None:
        """Make an impossible tool un-constructible -- fail on the declaring line."""
        if self.read_only and self.risk != RiskLevel.SAFE:
            raise ValueError(f"{self.name}: read_only tool must be SAFE, got {self.risk}")
        if self.cache_ttl_s is not None and not self.read_only:
            raise ValueError(f"{self.name}: only a read_only tool may be cached")
        if not self.read_only and self.plan_mode_safe:
            raise ValueError(f"{self.name}: a mutating tool is never plan_mode_safe")
        if self.timeout_s <= 0:
            raise ValueError(f"{self.name}: timeout_s must be > 0, got {self.timeout_s}")


# ==============================================================================
# 5 · AgentContext
# ==============================================================================

# NOTE ->> @dataclass. Dependencies passed IN, never imported globally. One instance per turn.
# NOTE ->> Identity/config fields: cwd, permission, session_id, request_id, provider, model.
# NOTE ->> Deadlines: deadline_at, started_at. Use time.monotonic() -- never time.time(), it jumps.
# NOTE ->> in_progress_tool_ids + confirmed_tool_calls: sets, for the approval round-trip in Step 8.
# NOTE ->> on_progress / on_permission_request: optional callables. None in batch/SDK runs --
# NOTE ->> that is what keeps the core free of any terminal dependency.
# NOTE ->> extras: dict -- per-turn scratch space. The file-state tracker lands here in Step 7.
# NOTE ->> __post_init__: normalise cwd -> Path(self.cwd).resolve().
@dataclass(slots=True)
class AgentContext:
    """Everything one turn needs, passed in rather than imported. Mutable by design.

    NOT frozen: in_progress_tool_ids, confirmed_tool_calls and extras all change
    as the turn runs, and __post_init__ rewrites cwd.
    """

    cwd: Path
    """Project root AND the containment boundary -- __post_init__ resolves it so
    every later comparison is against a real path, not a relative guess."""

    permission: PermissionContext
    """The session half of the permission decision; ToolSpec.risk is the tool half."""

    session_id: str
    """Spans many turns. Groups logs into one conversation."""

    request_id: str
    """This turn only. str, not int -- it is a correlation token, never arithmetic."""

    provider: str
    """"anthropic", "openai", ... str not enum: a new provider must not need an edit here."""

    model: str
    """Exact model id, for cost accounting and per-model behaviour switches."""

    deadline_at: float | None = None
    """monotonic timestamp the turn must finish by. None = no deadline, which is
    why remaining_s() returns inf rather than 0 -- absent is not expired."""

    started_at: float = field(default_factory=time.monotonic)
    """monotonic, never time.time(): wall clock jumps on NTP and would corrupt
    every duration measured across it."""

    in_progress_tool_ids: set[str] = field(default_factory=set)
    """Calls currently running. set: membership is the only question asked."""

    confirmed_tool_calls: set[str] = field(default_factory=set)
    """Calls the user approved. Keyed by tool_call_id, not tool name -- approval
    is granted to one specific call, and must not leak to the next one."""

    on_progress: Callable[[str], None] | None = None
    """Optional sink for progress lines. None in batch/SDK runs."""

    on_permission_request: Callable[[ToolSpec, dict[str, Any]], PermissionResult] | None = None
    """Optional prompter. None means nobody can answer, so ASK becomes DENY --
    this is what keeps the core free of any terminal dependency."""

    extras: dict[str, Any] = field(default_factory=dict)
    """Per-turn scratch space. dict so a later step can add state without
    reopening this file -- the file-state tracker lands here in Step 7."""

    def __post_init__(self) -> None:
        """Normalise cwd once, so containment checks compare resolved to resolved."""
        self.cwd = Path(self.cwd).resolve()

    # -- deadline helpers ------------------------------------------------------

    def remaining_s(self) -> float:
        """Seconds left in the turn. inf when unbounded, floored at 0 when past."""
        if self.deadline_at is None:
            return float("inf")
        return max(0.0, self.deadline_at - time.monotonic())

    def budget_for(self, spec: ToolSpec) -> float:
        """The tool's own ceiling, clipped by what is left of the turn."""
        return min(spec.timeout_s, self.remaining_s())

    def elapsed_ms(self) -> float:
        return (time.monotonic() - self.started_at) * 1000.0

    def confirmed(self, tool_call_id: str) -> bool:
        return tool_call_id in self.confirmed_tool_calls

    # -- path safety -----------------------------------------------------------

    def resolve_in_project(self, raw: str | Path) -> Path:
        """The one chokepoint for path traversal. Every filesystem tool routes here.

        .resolve() FIRST, then check containment -- checked the other way round, a
        symlink inside the project walks straight out and passes.
        """
        candidate = Path(raw)
        target = (candidate if candidate.is_absolute() else self.cwd / candidate).resolve()
        if target != self.cwd and not target.is_relative_to(self.cwd):
            raise ValueError(f"path escapes project root: {raw!r} -> {target}")
        return target


# ==============================================================================
# Gate  ->  tests/agent/test_contracts.py
# ==============================================================================

# NOTE ->> resolve_in_project("../../../etc/passwd") raises ValueError.
# NOTE ->> resolve_in_project("src/x.py") returns a path under cwd.
# NOTE ->> a symlink pointing outside the project is rejected.
# NOTE ->> ToolSpec(read_only=True, risk=HIGH, ...) raises.
# NOTE ->> ToolSpec(read_only=False, plan_mode_safe=True, ...) raises.
