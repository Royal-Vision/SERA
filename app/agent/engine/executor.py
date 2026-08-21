"""The tool execution engine.

This is `docs/tools.md`'s `execute_tool_call`, hardened. The pipeline is:

    resolve name -> repair args -> coerce -> validate -> preconditions
        -> permission -> plan batches -> execute -> shape result

Two design commitments drive everything here.

**1. An error message is a prompt.** Whatever this engine returns becomes the model's
next input. `"ValidationError"` teaches the model nothing and it will make the same
mistake again. `"top_n must be <= 10, you sent 50"` gets it right on the retry. Every
error path here is written to be actionable.

**2. Failure is contained, never propagated.** A tool raising must degrade to a
`ToolResult` the model can read. An exception escaping the engine kills the turn, which
is the single worst outcome -- the user loses all in-flight work.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import ValidationError

from app.agent.base import PermissionPolicy, Tool, ToolRegistry
from app.agent.contracts import (
    AgentContext,
    Decision,
    PermissionResult,
    ToolResult,
)
from app.agent.engine.repair import (
    RepairLog,
    coerce_to_schema,
    repair_json,
    resolve_tool_name,
)

__all__ = ["ToolCall", "ToolOutcome", "Outcome", "ToolEngine"]


class Outcome(StrEnum):
    OK = "ok"
    INVALID_ARGS = "invalid_args"
    UNKNOWN_TOOL = "unknown_tool"
    DENIED = "denied"
    NEEDS_APPROVAL = "needs_approval"
    PRECONDITION = "precondition"
    TIMEOUT = "timeout"
    ERROR = "error"
    CIRCUIT_OPEN = "circuit_open"


@dataclass(slots=True)
class ToolCall:
    """One tool invocation requested by the model."""

    id: str
    name: str
    raw_args: Any  # dict, or a string that still needs parsing


@dataclass(slots=True)
class ToolOutcome:
    call_id: str
    tool_name: str
    outcome: Outcome
    result: ToolResult
    duration_ms: float = 0.0
    repairs: list[str] = field(default_factory=list)
    over_budget: bool = False


# ──────────────────────────────────────────────────────────────────────────────
# Circuit breaker
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class _Circuit:
    """Stops a persistently failing tool from burning the whole turn.

    A model that gets an error will often retry the same call. If the tool is broken
    (bad credentials, unreachable service) that loop can consume every remaining step.
    After `threshold` consecutive failures the tool short-circuits with a message that
    tells the model to stop trying.
    """

    threshold: int = 3
    cooldown_s: float = 30.0
    failures: int = 0
    opened_at: float = 0.0

    def record(self, ok: bool) -> None:
        if ok:
            self.failures = 0
            self.opened_at = 0.0
        else:
            self.failures += 1
            if self.failures >= self.threshold:
                self.opened_at = time.monotonic()

    @property
    def open(self) -> bool:
        if not self.opened_at:
            return False
        if time.monotonic() - self.opened_at > self.cooldown_s:
            self.failures = 0
            self.opened_at = 0.0
            return False
        return True


# ──────────────────────────────────────────────────────────────────────────────
# Engine
# ──────────────────────────────────────────────────────────────────────────────


class ToolEngine:
    """Validates, authorises and executes tool calls."""

    def __init__(
        self,
        registry: ToolRegistry,
        policy: PermissionPolicy | None = None,
        *,
        max_parallel: int = 8,
    ) -> None:
        self.registry = registry
        self.policy = policy or PermissionPolicy()
        self._circuits: dict[str, _Circuit] = {}
        self._sem = asyncio.Semaphore(max_parallel)

    # -- public API -----------------------------------------------------------

    async def execute_many(
        self, calls: list[ToolCall], ctx: AgentContext
    ) -> list[ToolOutcome]:
        """Run a batch, parallelising everything that is safe to parallelise.

        Ordering guarantee: results come back in the order requested, regardless of
        completion order. Models reason about tool results positionally, so shuffling
        them causes confusing downstream errors.
        """
        if not calls:
            return []
        if len(calls) == 1:
            return [await self.execute(calls[0], ctx)]

        batches = self._plan_batches(calls, ctx)
        results: dict[str, ToolOutcome] = {}

        for batch in batches:
            if len(batch) == 1:
                outcome = await self.execute(batch[0], ctx)
                results[outcome.call_id] = outcome
            else:
                done = await asyncio.gather(
                    *(self._execute_guarded(c, ctx) for c in batch)
                )
                for outcome in done:
                    results[outcome.call_id] = outcome

        return [results[c.id] for c in calls]

    async def execute(self, call: ToolCall, ctx: AgentContext) -> ToolOutcome:
        started = time.monotonic()
        log = RepairLog()

        # 1. Resolve the tool name, tolerating near-misses.
        known = [t.name for t in self.registry]
        resolved = resolve_tool_name(call.name, known, log)
        if resolved is None:
            return self._finish(
                call, call.name, Outcome.UNKNOWN_TOOL,
                ToolResult.error(
                    f"Unknown tool {call.name!r}. Available tools: {', '.join(sorted(known))}."
                ),
                started, log,
            )

        tool = self.registry.get(resolved)
        assert tool is not None

        # 2. Circuit breaker.
        circuit = self._circuits.setdefault(resolved, _Circuit())
        if circuit.open:
            return self._finish(
                call, resolved, Outcome.CIRCUIT_OPEN,
                ToolResult.error(
                    f"{resolved} has failed {circuit.failures} times in a row and is "
                    f"temporarily disabled. Try a different approach rather than "
                    f"calling it again."
                ),
                started, log,
            )

        # 3. Parse + repair + coerce + validate.
        try:
            args_dict = repair_json(call.raw_args, log)
        except ValueError as exc:
            return self._finish(
                call, resolved, Outcome.INVALID_ARGS,
                ToolResult.error(f"Could not read the arguments for {resolved}: {exc}"),
                started, log,
            )

        schema = tool.json_schema()
        args_dict = coerce_to_schema(args_dict, schema, log)

        try:
            args = tool.validate(args_dict)
        except ValidationError as exc:
            return self._finish(
                call, resolved, Outcome.INVALID_ARGS,
                ToolResult.error(_actionable_validation_error(resolved, exc, schema)),
                started, log,
            )

        # 4. Permission.
        decision: PermissionResult = self.policy.check(tool, args, ctx)
        if decision.decision is Decision.DENY:
            return self._finish(
                call, resolved, Outcome.DENIED,
                ToolResult.error(f"Permission denied for {resolved}: {decision.reason}"),
                started, log,
            )
        if decision.decision is Decision.ASK and not ctx.confirmed(call.id):
            return self._finish(
                call, resolved, Outcome.NEEDS_APPROVAL,
                ToolResult.error(
                    f"{resolved} needs your approval before it can run "
                    f"({decision.reason})."
                ),
                started, log,
            )

        # 5. Execute.
        ctx.in_progress_tool_ids.add(call.id)
        try:
            async with self._sem:
                result = await tool.run(args_dict, ctx)
        finally:
            ctx.in_progress_tool_ids.discard(call.id)

        circuit.record(ok=not result.is_error)
        outcome = Outcome.ERROR if result.is_error else Outcome.OK
        if result.metadata.get("timed_out"):
            outcome = Outcome.TIMEOUT

        return self._finish(call, resolved, outcome, result, started, log, spec_budget=tool.spec.budget_ms)

    # -- internals ------------------------------------------------------------

    async def _execute_guarded(self, call: ToolCall, ctx: AgentContext) -> ToolOutcome:
        """gather() must never see an exception, or sibling calls get cancelled."""
        try:
            return await self.execute(call, ctx)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - deliberate boundary
            from app.configs.logger import get_logger

            get_logger().exception("Engine failure on %s", call.name)
            return ToolOutcome(
                call_id=call.id,
                tool_name=call.name,
                outcome=Outcome.ERROR,
                result=ToolResult.error(f"{call.name} failed: {type(exc).__name__}: {exc}"),
            )

    def _plan_batches(
        self, calls: list[ToolCall], ctx: AgentContext
    ) -> list[list[ToolCall]]:
        """Group calls into batches that are safe to run concurrently.

        Two rules:
          * a tool that is not `concurrency_safe` runs alone;
          * two calls that touch the same path do not share a batch, even if both
            tools are individually concurrency-safe -- a read racing a write on one
            file is a genuine correctness bug, not just a performance question.
        """
        batches: list[list[ToolCall]] = []
        current: list[ToolCall] = []
        claimed: set[str] = set()

        for call in calls:
            tool = self._peek(call.name)
            safe = tool.spec.concurrency_safe if tool else False
            paths = _paths_touched(call)

            conflicts = bool(paths & claimed)
            if not safe or conflicts:
                if current:
                    batches.append(current)
                    current, claimed = [], set()
                if not safe:
                    batches.append([call])
                    continue

            current.append(call)
            claimed |= paths

        if current:
            batches.append(current)
        return batches

    def _peek(self, name: str) -> Tool[Any] | None:
        known = [t.name for t in self.registry]
        resolved = resolve_tool_name(name, known)
        return self.registry.get(resolved) if resolved else None

    def _finish(
        self,
        call: ToolCall,
        tool_name: str,
        outcome: Outcome,
        result: ToolResult,
        started: float,
        log: RepairLog,
        spec_budget: int | None = None,
    ) -> ToolOutcome:
        duration = (time.monotonic() - started) * 1000
        return ToolOutcome(
            call_id=call.id,
            tool_name=tool_name,
            outcome=outcome,
            result=result,
            duration_ms=round(duration, 2),
            repairs=list(log.repairs),
            over_budget=bool(spec_budget and duration > spec_budget),
        )


# ──────────────────────────────────────────────────────────────────────────────
# Error rendering
# ──────────────────────────────────────────────────────────────────────────────


def _actionable_validation_error(
    tool_name: str, exc: ValidationError, schema: dict[str, Any]
) -> str:
    """Turn a Pydantic error into something a model can act on.

    Pydantic's default output describes what is wrong. A model needs to be told what
    *right* looks like, so we append the offending field's constraints.
    """
    props: dict[str, Any] = schema.get("properties", {})
    required: list[str] = schema.get("required", [])
    lines: list[str] = [f"Invalid arguments for {tool_name}:"]

    for err in exc.errors()[:5]:
        loc = ".".join(str(p) for p in err["loc"]) or "(root)"
        msg = err["msg"]
        hint = ""

        prop = props.get(str(err["loc"][0])) if err["loc"] else None
        if err["type"] == "extra_forbidden":
            hint = f" -- valid parameters are: {', '.join(sorted(props))}"
        elif prop:
            bits = []
            if "type" in prop:
                bits.append(f"type={prop['type']}")
            if "enum" in prop:
                bits.append(f"one of {prop['enum']}")
            for k in ("minimum", "maximum", "minLength", "maxLength"):
                if k in prop:
                    bits.append(f"{k}={prop[k]}")
            if bits:
                hint = f" -- expected {', '.join(bits)}"

        lines.append(f"  - {loc}: {msg}{hint}")

    missing = [r for r in required if not any(r in str(e["loc"]) for e in exc.errors())]
    if missing and any(e["type"] == "missing" for e in exc.errors()):
        lines.append(f"  Required parameters: {', '.join(required)}")

    return "\n".join(lines)


_PATH_KEYS = ("path", "file_path", "filename", "target", "dest", "destination")


def _paths_touched(call: ToolCall) -> set[str]:
    """Best-effort write-set for conflict detection.

    Intentionally cheap and tolerant: this runs before validation, so the arguments
    may still be malformed. A missed path costs parallelism, never correctness --
    unsafe tools already run alone.
    """
    args = call.raw_args
    if not isinstance(args, dict):
        return set()
    return {
        str(args[k]).replace("\\", "/").lstrip("./")
        for k in _PATH_KEYS
        if k in args and isinstance(args[k], (str, bytes))
    }
