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
    def ok(cls, content: str, **meta: Any) -> ToolResult:
        return cls(content=content, metadata=meta)

    @classmethod
    def error(cls, message: str, **meta: Any) -> ToolResult:
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

# -- deadline helpers ----------------------------------------------------------
# NOTE ->> remaining_s(): deadline_at - now, floored at 0. Return inf when no deadline is set.
# NOTE ->> budget_for(spec): min(spec.timeout_s, remaining_s()) -- tool ceiling clipped by turn budget.
# NOTE ->> elapsed_ms() and confirmed(tool_call_id): one-liners.

# -- path safety ---------------------------------------------------------------
# NOTE ->> resolve_in_project(raw): THE chokepoint for path traversal. Every fs tool routes here.
# NOTE ->> Call .resolve() FIRST, then check containment -- otherwise a symlink walks straight out.
# NOTE ->> Absolute input is allowed but still checked; relative resolves against cwd.
# NOTE ->> Raise ValueError on escape. Accept the root itself AND anything under it.


# ==============================================================================
# Gate  ->  tests/agent/test_contracts.py
# ==============================================================================

# NOTE ->> resolve_in_project("../../../etc/passwd") raises ValueError.
# NOTE ->> resolve_in_project("src/x.py") returns a path under cwd.
# NOTE ->> a symlink pointing outside the project is rejected.
# NOTE ->> ToolSpec(read_only=True, risk=HIGH, ...) raises.
# NOTE ->> ToolSpec(read_only=False, plan_mode_safe=True, ...) raises.
