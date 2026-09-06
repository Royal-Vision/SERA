# Building a tool system in Python

This is a practical Python/Pydantic v2 equivalent of the `Tool.ts` contract in
this repository. Start with this small design; add rendering, agents, hooks,
plugins, and MCP only after the core loop works.

## What `Tool.ts` becomes in Python

| This repository | Python equivalent |
| --- | --- |
| `Tool<Input, Output>` | `Tool` protocol plus a Pydantic input model |
| Zod `inputSchema` | `BaseModel.model_json_schema()` |
| `ToolUseContext` | `ToolContext` dataclass |
| `checkPermissions` and `canUseTool` | `PermissionPolicy.authorize(...)` |
| `call(...)` | `async def call(...)` |
| `ToolResult` | `ToolResult` Pydantic model |
| `buildTool(...)` defaults | a small base class with safe defaults |

The important separation is:

```text
tool definition → validate arguments → permission decision → execute tool
       ↑                                                      ↓
    JSON Schema                                           ToolResult
       ↑                                                      ↓
      model  ←──────────────── tool result message ───────────┘
```

## Minimal types

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field


class ToolResult(BaseModel):
    """The value returned to the model after a tool finishes."""

    model_config = ConfigDict(extra="forbid")
    content: str
    is_error: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass
class PermissionContext:
    # Keep policy separate from UI. Your UI can ask the user if needed.
    mode: str = "default"  # for example: default, accept_edits, plan
    always_allow: set[str] = field(default_factory=set)
    always_deny: set[str] = field(default_factory=set)


@dataclass
class ToolContext:
    """Dependencies supplied by the app, rather than imported globally."""

    cwd: Path
    permission: PermissionContext
    messages: list[dict[str, Any]] = field(default_factory=list)
    in_progress_tool_ids: set[str] = field(default_factory=set)
    agent_id: str | None = None

    # Optional callbacks: analogous to the optional REPL callbacks in Tool.ts.
    on_progress: Any | None = None
    on_status: Any | None = None


InputT = TypeVar("InputT", bound=BaseModel)


class Tool(Protocol[InputT]):
    name: str
    description: str
    input_model: type[InputT]

    async def call(self, args: InputT, context: ToolContext) -> ToolResult:
        ...

    def is_read_only(self, args: InputT) -> bool:
        ...

    def is_concurrency_safe(self, args: InputT) -> bool:
        ...
```

`input_model` does two jobs, just as Zod does in `Tool.ts`:

```python
# Send to the model when describing the tool.
json_schema = ReadFileInput.model_json_schema()

# Validate arguments the model returned before the tool can run.
arguments = ReadFileInput.model_validate(raw_model_arguments)
```

## Implement one safe tool first

```python
class ReadFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="Path to a UTF-8 text file relative to the project")
    start_line: int = Field(default=1, ge=1)
    max_lines: int = Field(default=200, ge=1, le=2_000)


class ReadFileTool:
    name = "read_file"
    description = "Read a text file from the current project."
    input_model = ReadFileInput

    def is_read_only(self, args: ReadFileInput) -> bool:
        return True

    def is_concurrency_safe(self, args: ReadFileInput) -> bool:
        return True

    async def call(self, args: ReadFileInput, context: ToolContext) -> ToolResult:
        project_root = context.cwd.resolve()
        path = (project_root / args.path).resolve()

        # Prevent paths such as ../../secrets.txt from escaping the project.
        if path != project_root and project_root not in path.parents:
            return ToolResult(content="Path is outside the project.", is_error=True)
        if not path.is_file():
            return ToolResult(content=f"File not found: {args.path}", is_error=True)

        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            return ToolResult(content="Only UTF-8 text files are supported.", is_error=True)

        start = args.start_line - 1
        selected = lines[start : start + args.max_lines]
        numbered = "\n".join(
            f"{line_no}: {line}"
            for line_no, line in enumerate(selected, start=args.start_line)
        )
        return ToolResult(content=numbered or "(no lines in range)")
```

## Registry, permission policy, and executor

The executor is the Python equivalent of the `findToolByName` and
`tool.call(...)` path in `services/tools/toolExecution.ts`.

```python
from typing import Any


class PermissionPolicy:
    async def authorize(
        self,
        tool: Tool[Any],
        arguments: BaseModel,
        context: ToolContext,
    ) -> bool:
        if tool.name in context.permission.always_deny:
            return False
        if tool.name in context.permission.always_allow:
            return True

        # Safe v1 policy: automatically allow read-only tools only.
        if tool.is_read_only(arguments):
            return True

        # Replace with a terminal prompt: Allow once / always / deny.
        return False


class ToolRegistry:
    def __init__(self, tools: list[Tool[Any]]) -> None:
        self._by_name = {tool.name: tool for tool in tools}

    def get(self, name: str) -> Tool[Any] | None:
        return self._by_name.get(name)

    def api_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_model.model_json_schema(),
            }
            for tool in self._by_name.values()
        ]


async def execute_tool_call(
    *,
    name: str,
    raw_arguments: dict[str, Any],
    tool_use_id: str,
    registry: ToolRegistry,
    policy: PermissionPolicy,
    context: ToolContext,
) -> ToolResult:
    tool = registry.get(name)
    if tool is None:
        return ToolResult(content=f"Unknown tool: {name}", is_error=True)

    try:
        arguments = tool.input_model.model_validate(raw_arguments)
    except Exception as error:  # In production, catch pydantic.ValidationError.
        return ToolResult(content=f"Invalid arguments for {name}: {error}", is_error=True)

    if not await policy.authorize(tool, arguments, context):
        return ToolResult(content=f"Permission denied for {name}.", is_error=True)

    context.in_progress_tool_ids.add(tool_use_id)
    try:
        return await tool.call(arguments, context)
    except Exception as error:
        # Log the full exception server-side; return a safe message to the model.
        return ToolResult(content=f"{name} failed: {error}", is_error=True)
    finally:
        context.in_progress_tool_ids.discard(tool_use_id)
```

Create the first registry like this:

```python
registry = ToolRegistry([ReadFileTool()])
```

Then your model loop must do only this:

1. Send `registry.api_definitions()` with the conversation to your model API.
2. If the response contains text, stream/display it.
3. If it contains a tool request, call `execute_tool_call(...)`.
4. Add the `ToolResult` as a tool-result message.
5. Call the model again until it returns a final text response.

## Mapping the selected `Tool.ts` callbacks

The selected TypeScript fields are application callbacks, not required methods
on every tool. In Python, prefer a small context object and optional callables:

```python
from collections.abc import Callable


@dataclass
class InteractiveToolContext(ToolContext):
    set_in_progress_tool_ids: Callable[[set[str]], None] | None = None
    set_has_interruptible_tool_in_progress: Callable[[bool], None] | None = None
    set_response_length: Callable[[int], None] | None = None
    set_stream_mode: Callable[[str], None] | None = None
    on_compact_progress: Callable[[str], None] | None = None
    set_sdk_status: Callable[[str], None] | None = None
    open_message_selector: Callable[[], None] | None = None
```

Only pass these in an interactive terminal app. A batch/SDK invocation can use
the base `ToolContext` and has no dependency on terminal UI state.

## Build order

1. `ReadFileTool`, registry, schema generation, and executor.
2. A `GlobTool`/search tool.
3. A write/edit tool with an explicit approval prompt.
4. A shell tool with command-specific allow/deny rules and timeouts.
5. JSONL session persistence and context limits.
6. Background tasks, subagents, hooks, MCP, and plugins.

Do not add a general-purpose `run_command` tool before validation and explicit
permissions work. File-read and search tools let you test the full agent/tool
loop with a much smaller security risk.
