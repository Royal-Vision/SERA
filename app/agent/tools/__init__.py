"""Concrete tools. One module per tool, each owning its input model and ToolSpec.

Import discipline is inherited from base.py: stdlib + pydantic, plus anyio for I/O.
Nothing here may import langchain or langgraph -- the registry is built on the fast
path, before the first protocol frame.

P0 -- the minimum safe coding loop, in build order:

    read.py     fs.read          allow in workspace
    glob.py     fs.search        allow in workspace
    grep.py     fs.search        allow in workspace
    edit.py     fs.write         ask; accept_edits may allow
    write.py    fs.write         ask; accept_edits may allow
    bash.py     process.spawn    command-specific policy, else ask   <- LAST

Later priorities (Agent, TodoWrite/Task*, AskUserQuestion, Skill, plan-mode,
ToolSearch, NotebookEdit, WebFetch/WebSearch, LSP, worktrees, MCP) are not in this
package yet. See the tool catalog for their contracts.
"""

# The registry is NOT built at import time. build_registry() is the entry point, so
# importing one tool for a test does not drag in every other tool's dependencies.

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.base import Tool

# TOOL-CAT-001: never create a placeholder implementation for a name the registry
# references but cannot resolve. An unresolved tool gets an availability record with a
# reason, and its schema is OMITTED from model requests entirely -- a stub that errors
# at call time costs a whole round-trip to learn what the registry already knew.
UNAVAILABLE: dict[str, str] = {
    "bash": (
        "implementation_unavailable: the classifier and process lifecycle in bash.py "
        "are complete, but PermissionPolicy (base.py §9) is still a stub. Registering "
        "bash before the policy is real would auto-allow process.spawn under the "
        "default ALLOW stub. Move it into build_registry() when Step 8 lands."
    ),
}


def build_registry(*, include_bash: bool = False) -> dict[str, "Tool"]:
    """Canonical name -> tool instance. Duplicate names are a construction error.

    Imports happen INSIDE the function on purpose: the module-level import graph is
    what the 400 ms handshake budget pays for, and a test that wants only ReadTool
    should not pay for subprocess, shlex and difflib.
    """
    from app.agent.tools.edit import EditTool
    from app.agent.tools.glob import GlobTool
    from app.agent.tools.grep import GrepTool
    from app.agent.tools.read import ReadTool
    from app.agent.tools.write import WriteTool

    tools: list[Tool] = [ReadTool(), GlobTool(), GrepTool(), EditTool(), WriteTool()]

    if include_bash:
        from app.agent.tools.bash import BashTool
        tools.append(BashTool())

    registry: dict[str, Tool] = {}
    for tool in tools:
        name = tool.spec.name
        if name in registry:
            raise ValueError(f"duplicate canonical tool name: {name}")
        # Aliases resolve OLD transcripts only (TOOL-012). They are deliberately NOT
        # added to this map: generated schemas expose canonical names exclusively, or
        # the model learns to call the deprecated one.
        registry[name] = tool
    return registry


def aliases() -> dict[str, str]:
    """alias -> canonical name, for resolving transcripts recorded before a rename."""
    registry = build_registry(include_bash=True)
    canonical = set(registry)
    mapping: dict[str, str] = {}
    for name, tool in registry.items():
        for alias in tool.spec.aliases:
            if alias in canonical:
                raise ValueError(f"alias collides with a canonical name: {alias}")
            if alias in mapping:
                raise ValueError(f"alias {alias} claimed by both {mapping[alias]} and {name}")
            mapping[alias] = name
    return mapping


# NOTE ->> CATALOG ACCEPTANCE TEST -- one parametrised test over every registered tool:
# NOTE ->>   a ToolSpec exists; input and output schemas compile; capability, side effect,
# NOTE ->>   permission default, timeout, concurrency and idempotency metadata are EXPLICIT;
# NOTE ->>   at least one permission fixture and one validation fixture exist; aliases do
# NOTE ->>   not collide; dynamic schema hash present; provenance recorded; unresolved tools
# NOTE ->>   are not model-visible.
# NOTE ->> This is the test that keeps the catalog and the code from drifting apart -- it
# NOTE ->> fails the moment somebody adds a tool without deciding its policy.
