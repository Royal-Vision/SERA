"""Tool base class and registry.

`docs/tools.md` defines `Tool` as a Protocol. A small ABC works better here because
almost every tool wants the same defaults (`read_only` from the spec, a permission key
derived from arguments, uniform timeout handling), and an ABC lets a tool override just
the one it cares about.

Nothing here imports langchain or langgraph. Tools are plain async callables with a
Pydantic input model; `app/agent/graph/adapter.py` converts them to LangChain tools only
when the graph is actually built. That keeps `sera --help` and `sera --version` off the
~1800 ms `import langgraph.graph` path.
"""

from __future__ import annotations

import asyncio
import inspect
from abc import ABC, abstractmethod
from typing import Any, ClassVar, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from app.agent.contracts import (
    AgentContext,
    Decision,
    PermissionContext,
    PermissionMode,
    PermissionResult,
    RiskLevel,
    ToolResult,
    ToolSpec,
)

InputT = TypeVar("InputT", bound=BaseModel)


class Tool(ABC, Generic[InputT]):
    """Base class for every tool.

    Subclasses define `spec` and `input_model`, then implement `call`.
    """

    spec: ClassVar[ToolSpec]
    input_model: ClassVar[type[BaseModel]]

    # -- identity -------------------------------------------------------------

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def description(self) -> str:
        return self.spec.description or (self.__doc__ or "").strip()

    def json_schema(self) -> dict[str, Any]:
        """The schema sent to the model. This is `docs/tools.md`'s `inputSchema`."""
        return self.input_model.model_json_schema()

    # -- behaviour flags ------------------------------------------------------
    # Default to the spec, but allow per-argument overrides. A `bash` tool is the
    # motivating case: `bash(ls)` is read-only, `bash(rm -rf)` is not.

    def is_read_only(self, args: InputT) -> bool:
        return self.spec.read_only

    def is_concurrency_safe(self, args: InputT) -> bool:
        return self.spec.concurrency_safe

    def risk_for(self, args: InputT) -> RiskLevel:
        return self.spec.risk

    def permission_key(self, args: InputT) -> str:
        """Stable string a permission rule can match against.

        Scoped to the tool so `bash(git *)` cannot accidentally allow `write_file`.
        """
        return self.name

    # -- execution ------------------------------------------------------------

    @abstractmethod
    async def call(self, args: InputT, ctx: AgentContext) -> ToolResult:
        """Do the work. Must not raise -- return `ToolResult.error(...)` instead."""

    def validate(self, raw: dict[str, Any]) -> InputT:
        """Validate model-produced arguments. Raises ValidationError."""
        return self.input_model.model_validate(raw)  # type: ignore[return-value]

    async def run(self, raw: dict[str, Any], ctx: AgentContext) -> ToolResult:
        """Validate -> enforce timeout -> execute. The one entry point callers use.

        Never raises. Every failure becomes a `ToolResult` the model can read and
        recover from; an exception escaping here would kill the whole turn.
        """
        try:
            args = self.validate(raw)
        except ValidationError as exc:
            return ToolResult.error(
                f"Invalid arguments for {self.name}: {_format_validation_error(exc)}"
            )

        timeout = ctx.budget_for(self.spec)
        if timeout <= 0:
            return ToolResult.error(f"{self.name} skipped: turn deadline already reached.")

        try:
            async with asyncio.timeout(timeout):
                return await self.call(args, ctx)
        except TimeoutError:
            return ToolResult.error(
                f"{self.name} timed out after {timeout:.1f}s.", timed_out=True
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - deliberate boundary
            # The full traceback goes to the log; the model gets a short, safe message.
            from app.configs.logger import get_logger

            get_logger().exception("Tool %s failed", self.name)
            return ToolResult.error(f"{self.name} failed: {type(exc).__name__}: {exc}")


def _format_validation_error(exc: ValidationError) -> str:
    """Compact, model-readable validation errors.

    Pydantic's default repr is multi-line and verbose; in an agent loop that verbosity
    is re-sent on every subsequent turn.
    """
    parts = []
    for err in exc.errors()[:5]:
        loc = ".".join(str(p) for p in err["loc"]) or "(root)"
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)


# ──────────────────────────────────────────────────────────────────────────────
# Permission policy
# ──────────────────────────────────────────────────────────────────────────────


class PermissionPolicy:
    """Decides allow / deny / ask. `docs/tools.md`'s `checkPermissions`.

    Deliberately pure and synchronous: no prompting, no I/O. The caller turns an
    `ASK` into whatever its UI is -- a terminal prompt, a LangGraph interrupt, or an
    automatic denial in a non-interactive run.
    """

    def check(self, tool: Tool[Any], args: Any, ctx: AgentContext) -> PermissionResult:
        perm: PermissionContext = ctx.permission
        key = tool.permission_key(args)
        read_only = tool.is_read_only(args)
        risk = tool.risk_for(args)

        # 1. Explicit denials always win, in every mode.
        if _matches(key, perm.always_deny):
            return PermissionResult(Decision.DENY, f"{key} is on the deny list", "always_deny")

        # 2. Plan mode is a hard wall: nothing may mutate, regardless of allow-lists.
        if perm.mode is PermissionMode.PLAN and not read_only:
            return PermissionResult(
                Decision.DENY,
                f"{tool.name} would modify state, and the session is in plan mode.",
                "plan_mode",
            )

        # 3. Read-only tools inside the project are always fine.
        if read_only:
            return PermissionResult(Decision.ALLOW, "read-only", "read_only")

        # 4. Bypass mode: everything else is allowed.
        if perm.mode is PermissionMode.BYPASS:
            return PermissionResult(Decision.ALLOW, "bypass mode", "bypass")

        # 5. Remembered approvals.
        if _matches(key, perm.always_allow):
            return PermissionResult(Decision.ALLOW, "on the allow list", "always_allow")
        if _matches(key, perm.session_allow):
            return PermissionResult(Decision.ALLOW, "approved earlier this session", "session")

        # 6. accept_edits auto-approves ordinary file edits, but never HIGH risk.
        if perm.mode is PermissionMode.ACCEPT_EDITS and risk is not RiskLevel.HIGH:
            return PermissionResult(Decision.ALLOW, "accept-edits mode", "accept_edits")

        return PermissionResult(Decision.ASK, f"{key} needs approval", "default")


def _matches(key: str, patterns: set[str]) -> bool:
    """Exact match, or glob when the pattern contains a wildcard."""
    if key in patterns:
        return True
    if not patterns:
        return False
    from fnmatch import fnmatch

    return any(("*" in p or "?" in p) and fnmatch(key, p) for p in patterns)


# ──────────────────────────────────────────────────────────────────────────────
# Registry
# ──────────────────────────────────────────────────────────────────────────────


class ToolRegistry:
    """Name -> tool, plus the schema list handed to the model."""

    def __init__(self, tools: "list[Tool[Any]] | None" = None) -> None:
        self._by_name: dict[str, Tool[Any]] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool[Any]) -> None:
        if tool.name in self._by_name:
            raise ValueError(f"Duplicate tool name: {tool.name}")
        self._by_name[tool.name] = tool

    def get(self, name: str) -> "Tool[Any] | None":
        return self._by_name.get(name)

    def spec(self, name: str) -> "ToolSpec | None":
        tool = self._by_name.get(name)
        return tool.spec if tool else None

    def __len__(self) -> int:
        return len(self._by_name)

    def __iter__(self):
        return iter(self._by_name.values())

    def for_mode(self, mode: PermissionMode) -> "list[Tool[Any]]":
        """The tool subset to expose for a given permission mode.

        In plan mode the mutating tools are not merely denied -- they are not offered
        at all. A model that cannot see `write_file` does not waste a turn trying it
        and being refused.
        """
        if mode is PermissionMode.PLAN:
            return [t for t in self._by_name.values() if t.spec.plan_mode_safe]
        return list(self._by_name.values())

    def schemas(self, mode: PermissionMode = PermissionMode.DEFAULT) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.json_schema(),
            }
            for tool in self.for_mode(mode)
        ]


def build_default_registry() -> ToolRegistry:
    """The standard tool set.

    Imported lazily by callers so `sera --help` never pays for it.
    """
    from app.agent.tools.edit import EditFileTool
    from app.agent.tools.glob import GlobTool
    from app.agent.tools.grep import GrepTool
    from app.agent.tools.read import ReadFileTool
    from app.agent.tools.write import WriteFileTool

    return ToolRegistry(
        [
            ReadFileTool(),
            GlobTool(),
            GrepTool(),
            EditFileTool(),
            WriteFileTool(),
        ]
    )
