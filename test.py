"""Generics + Protocols -- exercise skeleton. Implement each section in order.

Target:      Python 3.14.7  (PEP 695 type params, no typing.Generic / TypeVar)
Run:         python3 test.py
Type-check:  uvx mypy --strict test.py     <- the half that actually matters

Rule for every section: the static assertions must pass a --strict check, not
just the runtime print.
"""

from dataclasses import dataclass
from typing import Any, Protocol, assert_type, get_origin, runtime_checkable

from pydantic import BaseModel


# ==============================================================================
# 0 · A NON-generic protocol -- and the hole it cannot fill
# ==============================================================================
# TODO
#   - Writable: one method `write(data: str) -> None`.
#   - Readable: one method `read() -> str`.
#   - do_write / do_read: free functions taking the protocol, not a base class.
#   - Author: matches BOTH structurally while inheriting nothing.
# Then answer for yourself: why can this protocol never serve two tools that
# take different argument models?

from abc import abstractmethod


class Writable(Protocol): 
    @abstractmethod
    def write(self, data: str) -> None: ...



class Readable(Protocol): 
    @abstractmethod
    def read(self) -> str : ...


def do_write(w: Writable, data: str) -> None: 
    w.write(data=data)


def do_read(r: Readable) -> str: 
    return r.read()


class Author: 
    def __init__(self, name: str):
        self.name: str = name

    def write(self, data: str) -> None:
        self.name = data

    def read(self) -> str:
        return self.name





# ==============================================================================
# 1 · What a type parameter actually IS
# ==============================================================================
# TODO
#   - first_any(items: list[Any]) -> Any      the information-destroying version
#   - first[T](items: list[T]) -> T           the information-preserving version
#   - _section1: prove the difference with assert_type on both, for at least two
#     element types, and leave the illegal attribute access commented out with a
#     note on which one the checker catches.


def first_any(items: list[Any]) -> Any: ...


def first[T](items: list[T]) -> T: ...


def _section1() -> None: ...


# ==============================================================================
# 2 · Generic classes -- the parameter is remembered by the INSTANCE
# ==============================================================================
# TODO
#   - Box[T]: __init__(value: T), get() -> T, replace(value: T) -> Box[T].
#   - _section2: assert_type the instance and get(); then show with get_origin
#     that the parameter is ERASED at runtime -- Box[str] and Box[int] share one
#     class object.


class Box[T]: ...


def _section2() -> None: ...


# ==============================================================================
# 3 · Bounds -- narrowing what is allowed to fill the hole
# ==============================================================================
# TODO
#   - ReadInput  (file_path: str, offset: int = 0) and ReadOutput
#     (content: str, line_count: int) as pydantic models.
#   - dump[M: BaseModel](model: M) -> str calling model_dump_json() -- legal only
#     because of the bound.
#   - _section3: assert_type the result, comment out a call that the bound must
#     reject.
# Also note the three flavours you are NOT using here and when each applies:
#   constrained  def f[T: (int, str)]      exactly int or exactly str
#   variadic     def g[*Ts](*a: *Ts)       tuple of unknown length
#   paramspec    def h[**P](fn: ...)       forwards a signature (decorators)


class ReadInput(BaseModel): ...


class ReadOutput(BaseModel): ...


def dump[M: BaseModel](model: M) -> str: ...


def _section3() -> None: ...


# ==============================================================================
# 4 · The real target -- a GENERIC PROTOCOL
# ==============================================================================
# This is `Protocol[InputT, OutputT]` from the SRS: the reason ToolExecutor can
# stay ignorant of every concrete tool while still being typed.
#
# TODO
#   - ToolSpec: frozen slots dataclass, name + version.
#   - Tool[InputT: BaseModel, OutputT: BaseModel](Protocol) with
#       spec -> ToolSpec as a read-only @property (NOT a bare attribute -- work
#         out why the bare form rejects an implementer that uses a property),
#       async validate_semantics(args: InputT) -> None,
#       async execute(args: InputT) -> OutputT.
#   - ReadTool: satisfies Tool[ReadInput, ReadOutput] structurally, no base
#     class; rejects a negative offset in validate_semantics.
#   - run_tool[I: BaseModel, O: BaseModel](tool: Tool[I, O], args: I) -> O.
#   - _section4: assert_type the result is exactly ReadOutput -- not Any, not
#     BaseModel -- and comment out the call where I is pinned by `tool` and
#     `args` no longer fits.


@dataclass(frozen=True, slots=True)
class ToolSpec: ...


class Tool[InputT: BaseModel, OutputT: BaseModel](Protocol): ...


class ReadTool: ...


async def run_tool[I: BaseModel, O: BaseModel](tool: Tool[I, O], args: I) -> O: ...


async def _section4() -> None: ...


# ==============================================================================
# 5 · Variance -- why InputT and OutputT behave differently
# ==============================================================================
# You do NOT declare variance under PEP 695; the checker infers it per parameter
# from HOW you used it. Work out the two directions before writing the code:
#
#   OutputT -- return position only    -> ?     Tool[ReadInput, ReadOutput]
#                                               vs Tool[ReadInput, BaseModel]
#   InputT  -- parameter position only -> ?     Tool[BaseModel, ReadOutput]
#                                               vs Tool[ReadInput, ReadOutput]
#   used in BOTH                       -> ?     (what list[T] is, and why
#                                               list[int] is not a list[object])
#
# TODO
#   - AnyModelTool: accepts any BaseModel, returns ReadOutput.
#   - _section5: two annotated assignments that only typecheck if you got the
#     directions right -- one exercising each parameter.


class AnyModelTool: ...


def _section5() -> None: ...


# ==============================================================================
# 6 · HOW TO TEST ALL OF THIS
# ==============================================================================
# Generics are a STATIC contract -- annotations are erased before pytest runs,
# so layer B is the one that actually holds the line.
#
# A · runtime tests (pytest)
#     TODO write two: run_tool returns a usable ReadOutput; a negative offset
#     raises from validate_semantics.
#
# B · static tests -- these fail the BUILD, not the test run
#     B1  assert_type(expr, T)          pin what the executor returns (§4).
#     B2  conformance assertion         one line per tool, below.
#     B3  negative test                 under mypy --strict (which turns on
#                                       --warn-unused-ignores) an ignore on a
#                                       line that stops erroring is itself an
#                                       error -- so it asserts "this stays
#                                       illegal". Pin the EXACT code: run it
#                                       once uncommented and copy what mypy
#                                       prints. pyright equivalent:
#                                       reportUnnecessaryTypeIgnoreComment.
#     B4  reveal_type(x)                debugging only, delete before commit.
#
# C · wire it up: there is no type checker in pyproject.toml and the workflow
#     only deploys, so today every Protocol in app/agent/ is a comment.
#         uv add --dev mypy
#         uvx mypy --strict app/agent/      <- then the same line in CI
#
# TODO
#   - _assert_read_tool_conforms: B2 for ReadTool. Never called at runtime.
#   - the B3 line for run_tool, commented, with its error code pinned.


def _assert_read_tool_conforms(t: ReadTool) -> Tool[ReadInput, ReadOutput]: ...


# D · what still needs a RUNTIME test
# @runtime_checkable checks METHOD NAMES only -- not signatures, not types.
# TODO
#   - RuntimeTool: runtime_checkable protocol with `execute`.
#   - Impostor: has `execute` with the WRONG signature.
#   - _section6: show isinstance() passes anyway, and say what to use instead
#     when validating an MCP adapter at registration time.


@runtime_checkable
class RuntimeTool(Protocol): ...


class Impostor: ...


def _section6() -> None: ...


# ==============================================================================
# 7 · Applying it in SERA
# ==============================================================================
# TODO once §4-§6 are done:
#   - contracts.py:240-243 still uses the OLD spelling (module-level TypeVar +
#     Generic base). Port it to `class ToolResult[OutputT](BaseModel)`: the
#     parameter is scoped to the class, variance is inferred, no Generic base.
#     Note while porting that pydantic generics are NOT erased -- ToolResult[str]
#     builds a real concrete class that validates `output` at runtime.
#   - base.py:20-27, the Protocol-vs-ABC conflict:
#       publish  Tool[InputT, OutputT]  (Protocol)  <- what ToolExecutor accepts
#       ship     BaseTool               (ABC)       <- what built-ins subclass;
#                                                      json_schema(),
#                                                      permission_facts() defaults
#       pin them together with a B2-style assertion in the catalog acceptance
#       test from tools/__init__.py:31-38.
#     Adapters that cannot inherit still typecheck; built-ins still get defaults.


async def main() -> None:
    print("§0 non-generic protocol")
    author = Author("Alice")
    do_write(author, "Hello, World!")
    print(" ", do_read(author))

    print("\n§1 type parameters")

    # print("\n§2 generic classes")
    # _section2()

    # print("\n§3 bounds")
    # _section3()

    # print("\n§4 generic protocol")
    # await _section4()

    # print("\n§5 variance")
    # _section5()

    # print("\n§6 runtime_checkable is weak")
    # _section6()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
