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

# NOTE ->> Do NOT build the registry here at import time. Export a build_registry(...)
# NOTE ->> the entry point calls, so importing one tool for a test does not drag in every
# NOTE ->> other tool's dependencies.

# NOTE ->> TOOL-CAT-001: never create a placeholder implementation for a name the registry
# NOTE ->> references but cannot resolve. Return an availability record with reason
# NOTE ->> "implementation_unavailable" for operators, and OMIT the schema from model
# NOTE ->> requests entirely. A stub that errors at call time costs a whole round-trip to
# NOTE ->> learn what the registry already knew.

# NOTE ->> CATALOG ACCEPTANCE TEST -- one parametrised test over every registered tool:
# NOTE ->>   a ToolSpec exists; input and output schemas compile; capability, side effect,
# NOTE ->>   permission default, timeout, concurrency and idempotency metadata are EXPLICIT;
# NOTE ->>   at least one permission fixture and one validation fixture exist; aliases do
# NOTE ->>   not collide; dynamic schema hash present; provenance recorded; unresolved tools
# NOTE ->>   are not model-visible.
# NOTE ->> This is the test that keeps the catalog and the code from drifting apart -- it
# NOTE ->> fails the moment somebody adds a tool without deciding its policy.
