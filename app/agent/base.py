"""The Tool ABC, the registry, and a stub policy. Step 2 · Phase 02.

Sits directly on contracts.py and under everything else. Imported before the
first protocol frame is written, so the import budget here is real: contracts.py
~8 ms, this file ~148 ms (pydantic dominates), registry construction ~80 ms.
That is most of the 400 ms handshake -- nothing else may join the fast path.
"""

# NOTE ->> Import discipline, same as contracts.py: stdlib + pydantic ONLY.
# NOTE ->> No langchain, no langgraph, no torch. `import langgraph.graph` costs ~1800 ms.
# NOTE ->> You will need: asyncio, logging, ABC/abstractmethod, Generic/TypeVar/Any,
# NOTE ->>                BaseModel/ValidationError from pydantic, and contracts.
from typing import ANY, Generic, TypeVar
from abc import ABC, abstractmethod
from pydantic import BaseModel, ValidationError
import asyncio

# ==============================================================================
# 1 · The type variable
# ==============================================================================

# NOTE ->> InputT = TypeVar("InputT", bound=BaseModel). Tool is generic in its input model
# NOTE ->> so `args` is typed inside call() instead of being a bare dict.


# ==============================================================================
# 2 · Tool  --  ABC, not Protocol
# ==============================================================================

# NOTE ->> ABC over Protocol because nearly every tool wants the same defaults and
# NOTE ->> overrides exactly one. A Protocol makes every tool reimplement all of them.
# NOTE ->> Class attributes: spec: ToolSpec, input_model: type[InputT].
# NOTE ->> name property -> self.spec.name. One line, but every call site reads better.
# NOTE ->> json_schema(): the model's view of this tool. Build from input_model.model_json_schema().

# -- the four behaviour hooks --------------------------------------------------
# NOTE ->> is_read_only(args), is_concurrency_safe(args), risk_for(args), permission_key(args).
# NOTE ->> ALL FOUR TAKE args, and all four default to the spec. They look redundant today.
# NOTE ->> They are the entire reason bash(ls) can auto-allow while bash(rm -rf) prompts:
# NOTE ->> Step 12 overrides them per-command. Do NOT simplify them into properties --
# NOTE ->> that decision is unrecoverable without touching every tool and the policy.

# -- the abstract one ----------------------------------------------------------
# NOTE ->> async call(args: InputT, ctx: AgentContext) -> ToolResult. @abstractmethod.
# NOTE ->> This is the ONLY thing a tool must write. Everything above has a default.


# ==============================================================================
# 3 · run()  --  the no-raise rule
# ==============================================================================

# NOTE ->> async run(raw: dict, ctx) -> ToolResult. The engine calls THIS, never call().
# NOTE ->> An exception escaping here kills the turn and the user loses all in-flight work,
# NOTE ->> so every terminal state must come back as a ToolResult the model can read.
# NOTE ->> Order, and each step's failure mode:
# NOTE ->>   1. validate raw -> input_model. ValidationError -> ToolResult.error, NOT a raise.
# NOTE ->>      The message is a prompt: say which field, what was sent, what was expected.
# NOTE ->>   2. timeout = ctx.budget_for(self.spec). If <= 0, the turn deadline is already
# NOTE ->>      gone -- return an error rather than starting work that cannot land.
# NOTE ->>   3. async with asyncio.timeout(timeout): return await self.call(args, ctx)
# NOTE ->>   4. TimeoutError        -> ToolResult.error naming the tool and the seconds.
# NOTE ->>   5. CancelledError      -> RE-RAISE. The one exception that must escape:
# NOTE ->>      swallowing it breaks user cancellation and turn timeouts alike.
# NOTE ->>   6. Exception           -> log the traceback to STDERR, return a short message.
# NOTE ->>      Full traceback to the model is pure token cost and teaches it nothing.


# ==============================================================================
# 4 · ToolRegistry
# ==============================================================================

# NOTE ->> _by_name: dict[str, Tool]. register() REJECTS a duplicate name -- two tools
# NOTE ->> answering to one name is a silent wrong-tool bug, so make it an import-time crash.
# NOTE ->> get(name) -> Tool | None, spec(name) -> ToolSpec | None.

# NOTE ->> for_mode(mode) is the one that matters:
# NOTE ->>     if mode is PermissionMode.PLAN: keep only specs with plan_mode_safe.
# NOTE ->> Not offered, not merely denied. A model that cannot SEE write_file does not
# NOTE ->> spend a round-trip trying it -- against a roundtrips <= 4 budget that is real,
# NOTE ->> and a tool that never appears beats a refusal the user has to read.
# NOTE ->> schemas(mode) -> [t.json_schema() for t in self.for_mode(mode)].


# ==============================================================================
# 5 · PermissionPolicy  --  STUB until Step 8
# ==============================================================================

# NOTE ->> check(tool, args, ctx) -> PermissionResult. For now: always ALLOW,
# NOTE ->> rule="stub". The real 7-rule cascade is Step 8, once you have tools whose
# NOTE ->> decisions actually differ -- writing it now means testing it against nothing.
# NOTE ->> Keep the SIGNATURE final even though the body is a stub: the executor starts
# NOTE ->> calling this in Step 6 and must not change when the body gets real.
# NOTE ->> check() stays pure and SYNCHRONOUS -- no prompting, no I/O, no await.
# NOTE ->> The caller turns ASK into a terminal prompt / a graph interrupt / a denial.
# NOTE ->> That purity is exactly what makes Step 8's gate a table test with no mocking.


# ==============================================================================
# Gate  ->  tests/agent/test_base.py
# ==============================================================================

# NOTE ->> run() with invalid args returns is_error=True and does NOT raise.
# NOTE ->> a tool whose call() raises ZeroDivisionError returns is_error=True.
# NOTE ->> a tool that sleeps past timeout_s returns a timeout result.
# NOTE ->> registering two tools under one name raises.
# NOTE ->> registry.for_mode(PLAN) excludes every mutating tool.
# NOTE ->> `import app.agent.base` pulls in neither langchain nor langgraph.
