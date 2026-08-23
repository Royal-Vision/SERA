"""Core agent contracts -- Tool Contract SRS §01. The bottom of the stack.

A tool is not an async function. It is a VERSIONED CAPABILITY with schemas, risk
metadata, permission behaviour, concurrency rules, cancellation, progress, output
limits and audit evidence. Everything in this file exists so the executor can
decide all of that WITHOUT calling the tool.
"""

# NOTE ->> Import discipline: stdlib + pydantic ONLY. No langchain/langgraph/torch.
# NOTE ->> No `from __future__ import annotations` -- 3.14 defers annotations by default
# NOTE ->> (PEP 649). The SRS reference snippet has that import; it is a pre-3.14 artefact.

import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

NAME_PATTERN = r"[A-Za-z][A-Za-z0-9_.-]{0,127}"
"""TOOL-001. Canonical names are an API surface, so they get a grammar, not a habit."""


# ==============================================================================
# 1 · Enums   (StrEnum -- logged and serialised as-is)
# ==============================================================================

class SideEffect(StrEnum):
    """WHAT the tool changes. Policy keys on this, not on a read_only bool.

    A web GET is read-only and still crosses a network boundary; a message to
    another agent mutates no file and can still trigger external behaviour. One
    boolean cannot separate those, so it does not try.
    """

    NONE = "none"
    """Pure observation inside the workspace. The only class PLAN mode offers."""

    LOCAL_STATE = "local_state"
    """Session-scoped state -- todo list, mode. Dies with the session."""

    WORKSPACE_WRITE = "workspace_write"
    """Creates or modifies files under the workspace root."""

    PROCESS = "process"
    """Spawns a process. Unbounded by construction -- see bash."""

    NETWORK_READ = "network_read"
    """Leaves the machine to read. The channel prompt injection needs to exfiltrate."""

    EXTERNAL_WRITE = "external_write"
    """Changes state somebody else owns. Not revertible by us at any price."""

    DESTRUCTIVE = "destructive"
    """Irreversible local loss -- delete, force-push, worktree removal."""


class RiskLevel(StrEnum):
    """HOW MUCH damage, given the side effect. Four levels, per the SRS."""

    LOW = "low"
    """Trivially reverted -- a new file, a session toggle."""

    MEDIUM = "medium"
    """Modifies state that already mattered -- an edit to a tracked file."""

    HIGH = "high"
    """Irreversible, or escapes the workspace."""

    CRITICAL = "critical"
    """Credential access, CI/hook/settings writes, anything supply-chain shaped."""


class ConcurrencyClass(StrEnum):
    """Scheduler behaviour. Combined with resource_keys -- class alone is too coarse."""

    PARALLEL = "parallel"
    READ_PARALLEL = "read_parallel"
    """Overlaps with reads of the same resource, never with a write to it."""
    SERIAL_SESSION = "serial_session"
    SERIAL_WORKSPACE = "serial_workspace"
    EXCLUSIVE_RUNTIME = "exclusive_runtime"
    """No other call in the daemon may overlap. Use rarely -- it stalls every session."""


class Idempotency(StrEnum):
    """What a retry after an ambiguous crash is allowed to do."""

    PURE = "pure"
    """Safe to recompute. The ONLY class whose result may be cached."""
    IDEMPOTENT = "idempotent"
    """Repeating lands the same external state; reuse the terminal receipt."""
    DEDUPLICATED = "deduplicated"
    """Must pass a stable idempotency key to the downstream system."""
    NON_IDEMPOTENT = "non_idempotent"
    """Crash recovery STOPS for reconciliation. It never auto-retries."""


class InterruptBehavior(StrEnum):
    CANCEL = "cancel"
    FINISH = "finish"
    """Let it complete -- cancelling mid-write is worse than finishing."""
    NON_INTERRUPTIBLE = "non_interruptible"


class Decision(StrEnum):
    """Three outcomes -- which is exactly why a bool is not enough."""

    ALLOW = "allow"
    DENY = "deny"
    """Returns a tool result, not an exception: the model must SEE the refusal."""
    ASK = "ask"
    """Suspends. The policy never prompts itself -- the caller decides how."""


class ResultStatus(StrEnum):
    """Every terminal state. TOOL-009 requires exactly one of these per attempt."""

    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    FAILED = "failed"
    SKIPPED = "skipped"
    """The batch condition failed before this call ran -- it never started."""


class PermissionMode(StrEnum):
    """Session-wide posture. Chosen by the USER, never by the agent."""

    DEFAULT = "default"
    ACCEPT_EDITS = "accept_edits"
    PLAN = "plan"
    """Nothing may mutate. Mutating tools are NOT OFFERED, and it is checked before
    allow-lists, so no earlier approval can override it."""
    BYPASS = "bypass"
    """Everything auto-allowed EXCEPT always_deny. Never infer it from a missing TTY."""


class ToolCategory(StrEnum):
    FILESYSTEM = "filesystem"
    SEARCH = "search"
    SHELL = "shell"
    WEB = "web"
    AGENT = "agent"
    TASK = "task"
    INTERACTION = "interaction"
    IDE = "ide"
    MCP = "mcp"
    SETTINGS = "settings"
    AUTOMATION = "automation"
    INTERNAL = "internal"


class ErrorCode(StrEnum):
    """The taxonomy IS retry guidance -- the model reads the code and decides."""

    NOT_FOUND = "tool.not_found"
    SCHEMA_INVALID = "tool.schema_invalid"
    SEMANTIC_INVALID = "tool.semantic_invalid"
    PERMISSION_DENIED = "tool.permission_denied"
    PERMISSION_EXPIRED = "tool.permission_expired"
    """Approved arguments changed, or the lease expired. Ask again -- never assume."""
    CANCELLED = "tool.cancelled"
    TIMEOUT = "tool.timeout"
    CONFLICT = "tool.conflict"
    DEPENDENCY_UNAVAILABLE = "tool.dependency_unavailable"
    OUTPUT_INVALID = "tool.output_invalid"
    """The implementation violated its own output schema. A runtime defect --
    do NOT hand it to the model to repair."""
    INTERNAL = "tool.internal"


_RETRYABLE = frozenset({
    ErrorCode.SCHEMA_INVALID, ErrorCode.SEMANTIC_INVALID,
    ErrorCode.CONFLICT, ErrorCode.DEPENDENCY_UNAVAILABLE,
})


# ==============================================================================
# 2 · Errors, artifacts, progress
# ==============================================================================

class ToolError(BaseModel):
    """An error is DATA. No implementation exception crosses into the model loop."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: ErrorCode
    message: str
    """Written for the model to act on: what was wrong, what to send instead."""
    retryable: bool = False
    details: dict[str, Any] | None = None

    @classmethod
    def of(cls, code: ErrorCode, message: str, **details: Any) -> "ToolError":
        return cls(code=code, message=message,
                    retryable=code in _RETRYABLE, details=details or None)


class ArtifactRef(BaseModel):
    """A pointer to bytes that must not enter the context window.

    TOOL-010. A path in model content is NOT an authorization token -- reading the
    artifact still goes through authenticated REST.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: str
    media_type: str
    size_bytes: int
    sha256: str
    preview: str | None = None
    """Head/tail excerpt, so the model can decide whether it wants the whole thing."""


class ToolProgress(BaseModel):
    """Advisory and DROPPABLE (TOOL-014). Never the carrier of a terminal fact."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    call_id: str
    sequence: int
    """Monotonic per call -- consumers coalesce and drop, so they need ordering."""
    message: str | None = None
    completed_units: int | None = None
    total_units: int | None = None
    preview: str | None = None


# ==============================================================================
# 3 · ToolResult
# ==============================================================================



class ToolResult[OutputT](BaseModel):
    """One settled outcome, in two representations.

    `output` is the typed structure for the database, the SDK and tests.
    `model_content` is what the next model request carries -- compact and bounded,
    because in an agent loop it is re-sent on EVERY later turn.
    """

    model_config = ConfigDict(extra="forbid")

    status: ResultStatus
    model_content: str
    output: OutputT | None = None
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    error: ToolError | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    """Out-of-band facts for the engine and logs -- never shown to the model."""
    truncated: int = 0
    """How much was cut. An int, not a bool: the model needs to know HOW much it is
    missing to decide whether to ask for the rest."""

    @classmethod
    def ok(cls, model_content: str, output: OutputT | None = None,
           **metadata: Any) -> "ToolResult[OutputT]":
        return cls(status=ResultStatus.SUCCEEDED, model_content=model_content,
                   output=output, metadata=metadata)

    @classmethod
    def failure(cls, error: ToolError, *, status: ResultStatus = ResultStatus.FAILED,
                **metadata: Any) -> "ToolResult[OutputT]":
        return cls(status=status, model_content=error.message,
                   error=error, metadata=metadata)

    @property
    def is_error(self) -> bool:
        return self.status is not ResultStatus.SUCCEEDED


# ==============================================================================
# 4 · Timeout policy
# ==============================================================================

@dataclass(frozen=True, slots=True)
class TimeoutPolicy:
    """Three deadlines, because a tool can fail to finish in three different ways."""

    default_s: float
    max_s: float
    idle_s: float | None = None
    """No output for this long -- for shell and streaming. A process that produces
    nothing for 60 s is hung, even when the wall clock still has room."""
    model_may_lower: bool = True
    """The model may ask for LESS. It may never ask for more -- that is the point
    of a maximum."""

    def __post_init__(self) -> None:
        if self.default_s <= 0 or self.max_s <= 0:
            raise ValueError("timeouts must be > 0")
        if self.default_s > self.max_s:
            raise ValueError(f"default_s {self.default_s} exceeds max_s {self.max_s}")
        if self.idle_s is not None and self.idle_s <= 0:
            raise ValueError("idle_s must be > 0 when set")

    def resolve(self, requested_s: float | None) -> float:
        """Clamp a model-requested timeout into policy."""
        if requested_s is None:
            return self.default_s
        if not self.model_may_lower:
            return self.default_s
        return max(0.0, min(float(requested_s), self.max_s))


# ==============================================================================
# 5 · ToolSpec
# ==============================================================================

@dataclass(frozen=True, slots=True)
class ToolSpec[ArgsT: BaseModel, OutputT]:
    """Declared facts, read INSTEAD of asking the tool. Built once, never mutated.

    TOOL-007: defaults FAIL CLOSED. Incomplete metadata is a construction error,
    not a permissive runtime guess.
    """

    name: str
    """Canonical API name. Must match NAME_PATTERN."""

    version: str
    """Semantic. Bump when model-visible input, output or behaviour changes
    incompatibly -- the argument hash includes it, so a bump invalidates every
    approval issued against the old shape."""

    description: str
    """The model's only documentation. Costs tokens every turn: say what it is FOR."""

    input_model: type[ArgsT]
    """Strict boundary validator AND the JSON Schema source. One definition, not two."""

    output_adapter: TypeAdapter[OutputT]
    """TOOL-003. Validates success output before persistence or model delivery.
    A TypeAdapter, not a model, so internal values need not all become BaseModels."""

    category: ToolCategory
    side_effect: SideEffect
    risk_level: RiskLevel

    capabilities: frozenset[str]
    """Fine-grained policy labels -- "fs.read", "process.spawn", "network.http".
    frozenset: hashable, so a spec stays usable as a dict key and cannot be edited."""

    default_permission: Decision
    concurrency: ConcurrencyClass
    resource_keys: Callable[[BaseModel], tuple[str, ...]]
    """Deterministic lock keys FROM VALIDATED ARGS -- fs:/repo/a.py:write. The class
    says how it may overlap; these say with what."""

    timeout: TimeoutPolicy
    interrupt_behavior: InterruptBehavior
    idempotency: Idempotency
    max_inline_result_bytes: int

    aliases: tuple[str, ...] = ()
    """TOOL-012. Resolve OLD transcripts only; generated schemas expose canonical
    names exclusively, or the model learns to call the deprecated one."""

    deferred: bool = False
    """Schema withheld until discovery. Discovery does not grant permission."""

    always_load: bool = False
    availability: Callable[[], bool] | None = None
    """Evaluates runtime capability WITHOUT mutating state -- it runs on every
    registry snapshot."""

    cache_ttl_s: float | None = None
    """Only meaningful when idempotency is PURE."""

    # -- derived ---------------------------------------------------------------

    @property
    def read_only(self) -> bool:
        """Kept for ergonomics. The SRS is explicit that this alone is NOT a
        permission input -- policy reads capabilities and side_effect."""
        return self.side_effect is SideEffect.NONE

    @property
    def plan_mode_safe(self) -> bool:
        """Derived, not declared: a field could contradict side_effect, a property
        cannot. In PLAN, anything that mutates is not offered at all."""
        return self.side_effect is SideEffect.NONE

    def __post_init__(self) -> None:
        """Make an impossible tool un-constructible -- fail on the declaring line."""
        import re

        if not re.fullmatch(NAME_PATTERN, self.name):
            raise ValueError(f"{self.name!r}: name must match {NAME_PATTERN}")
        if not re.fullmatch(r"\d+\.\d+\.\d+", self.version):
            raise ValueError(f"{self.name}: version must be semantic, got {self.version!r}")
        if not self.capabilities:
            raise ValueError(f"{self.name}: capabilities must not be empty (TOOL-007)")
        if not self.description.strip():
            raise ValueError(f"{self.name}: description is the model's only documentation")

        # Fail closed: only a pure observation may default to allow.
        if (self.default_permission is Decision.ALLOW
                and self.side_effect is not SideEffect.NONE):
            raise ValueError(
                f"{self.name}: default_permission=allow requires side_effect=none, "
                f"got {self.side_effect}"
            )
        # Anything that leaves the machine or cannot be undone must interrupt.
        if (self.side_effect in (SideEffect.EXTERNAL_WRITE, SideEffect.DESTRUCTIVE)
                and self.default_permission is not Decision.ASK):
            raise ValueError(
                f"{self.name}: {self.side_effect} must default to ask"
            )
        if self.side_effect is SideEffect.NONE and self.risk_level is not RiskLevel.LOW:
            raise ValueError(f"{self.name}: side_effect=none implies risk_level=low")
        # A mutation cannot be freely parallel -- it needs at least a resource lock.
        if (self.side_effect is not SideEffect.NONE
                and self.concurrency is ConcurrencyClass.PARALLEL):
            raise ValueError(
                f"{self.name}: {self.side_effect} may not be concurrency=parallel"
            )
        if self.cache_ttl_s is not None and self.idempotency is not Idempotency.PURE:
            raise ValueError(f"{self.name}: only a pure tool may be cached")
        if self.max_inline_result_bytes <= 0:
            raise ValueError(f"{self.name}: max_inline_result_bytes must be > 0")
        if self.always_load and self.deferred:
            raise ValueError(f"{self.name}: always_load contradicts deferred")


# ==============================================================================
# 6 · Permission data   (policy itself lives in base.py)
# ==============================================================================

@dataclass(frozen=True, slots=True)
class PermissionFacts:
    """What the tool tells the policy. The tool never prompts anyone itself."""

    capabilities: frozenset[str]
    side_effect: SideEffect
    risk_level: RiskLevel
    resource_keys: tuple[str, ...]
    human_summary: str
    """One line, shown at the approval prompt. The user approves THIS sentence."""
    proposed_diff_artifact_id: str | None = None
    """Approval must show the exact diff, never a summary of it."""


@dataclass(slots=True)
class PermissionResult:
    decision: Decision
    reason: str = ""
    """Prose for a human. Reworded freely -- never key on it."""
    rule: str = ""
    """Stable token naming the branch that decided. Tests assert on THIS."""

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW


@dataclass(slots=True)
class PermissionContext:
    """Mutable session state. The policy reads it on every call and may grow it."""

    mode: PermissionMode
    always_allow: set[str] = field(default_factory=set)
    always_deny: set[str] = field(default_factory=set)
    """Wins in EVERY mode, BYPASS included. A deny-list bypass can override is a
    suggestion, not a deny-list."""
    session_allow: set[str] = field(default_factory=set)

    def remember_allow(self, key: str, persist: bool = False) -> None:
        """Route one approval. Default False: a misclick costs one session."""
        (self.always_allow if persist else self.session_allow).add(key)


# ==============================================================================
# 7 · Canonicalisation   (TOOL-011)
# ==============================================================================

def canonical_json(payload: Any) -> str:
    """Stable key order, no incidental whitespace. Two equal calls hash equal."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)


def argument_hash(tool_name: str, tool_version: str, arguments: Any) -> str:
    """The identity an approval is bound to.

    Version is in the hash on purpose: bumping a tool's version invalidates every
    approval issued against the old argument shape, which is what stops an approval
    surviving a change to what it meant.
    """
    payload = f"{tool_name}\x00{tool_version}\x00{canonical_json(arguments)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def execution_key(run_id: str, tool_call_id: str, arg_hash: str) -> str:
    """Deduplication identity across a reconnect -- see Idempotency."""
    return f"{run_id}:{tool_call_id}:{arg_hash}"


# ==============================================================================
# 8 · Context
# ==============================================================================

@dataclass(slots=True)
class AgentContext:
    """Per-TURN state. Mutable, and passed in rather than imported (TOOL-015).

    One instance per turn; ToolRuntimeContext is minted per CALL from it.
    """

    cwd: Path
    """Workspace root AND the containment boundary."""
    permission: PermissionContext
    session_id: str
    run_id: str
    turn_id: str
    actor_id: str
    provider: str
    model: str

    deadline_at: float | None = None
    """monotonic. None = unbounded, which is why remaining_s() returns inf: absent
    is not expired."""
    started_at: float = field(default_factory=time.monotonic)
    in_progress_tool_ids: set[str] = field(default_factory=set)
    confirmed_tool_calls: set[str] = field(default_factory=set)
    on_progress: Callable[[ToolProgress], None] | None = None
    on_permission_request: Callable[[PermissionFacts], PermissionResult] | None = None
    """None means nobody can answer, so ASK becomes DENY. That is what keeps the
    core free of any terminal dependency."""
    extras: dict[str, Any] = field(default_factory=dict)
    """Per-turn scratch space -- the file-state tracker lands here."""

    def __post_init__(self) -> None:
        self.cwd = Path(self.cwd).resolve()

    # -- deadlines -------------------------------------------------------------

    def remaining_s(self) -> float:
        if self.deadline_at is None:
            return float("inf")
        return max(0.0, self.deadline_at - time.monotonic())

    def budget_for[K: BaseModel, V](self, spec: ToolSpec[K, V], requested_s: float | None = None) -> float:
        """The tool's own policy, clipped by what is left of the turn."""
        return min(spec.timeout.resolve(requested_s), self.remaining_s())

    def elapsed_ms(self) -> float:
        return (time.monotonic() - self.started_at) * 1000.0

    def confirmed(self, tool_call_id: str) -> bool:
        return tool_call_id in self.confirmed_tool_calls

    # -- path safety -----------------------------------------------------------

    def resolve_in_project(self, raw: str | Path) -> Path:
        """The one chokepoint for path traversal. Every filesystem tool routes here.

        .resolve() FIRST, then check containment -- the other way round, a symlink
        inside the project walks straight out and passes.
        """
        candidate = Path(raw)
        target = (candidate if candidate.is_absolute() else self.cwd / candidate).resolve()
        if target != self.cwd and not target.is_relative_to(self.cwd):
            raise ValueError(f"path escapes workspace root: {raw!r} -> {target}")
        return target

    def runtime_for(self, tool_call_id: str, workspace_id: str = "default") -> "ToolRuntimeContext":
        """Mint the per-call context. Frozen, so a tool cannot edit its own identity."""
        return ToolRuntimeContext(
            session_id=self.session_id, run_id=self.run_id, turn_id=self.turn_id,
            tool_call_id=tool_call_id, workspace_id=workspace_id,
            workspace_root=self.cwd, actor_id=self.actor_id,
            permission_mode=self.permission.mode, turn=self,
        )


@dataclass(frozen=True, slots=True)
class ToolRuntimeContext:
    """Per-CALL identity and wiring. Frozen: trusted, process-local, not JSON.

    A plain dataclass rather than a BaseModel because it carries units of work,
    adapters, clients and callbacks -- none of which mean anything serialised.
    Public requests and persisted records stay Pydantic.
    """

    session_id: str
    run_id: str
    turn_id: str
    tool_call_id: str
    workspace_id: str
    workspace_root: Path
    actor_id: str
    permission_mode: PermissionMode
    turn: AgentContext
    """The per-turn context -- deadlines, path chokepoint, progress sink."""

    def emit_progress(self, progress: ToolProgress) -> None:
        """Advisory. Dropped when nobody is listening -- never a terminal fact."""
        if self.turn.on_progress is not None:
            self.turn.on_progress(progress)

    def resolve_in_project(self, raw: str | Path) -> Path:
        return self.turn.resolve_in_project(raw)
