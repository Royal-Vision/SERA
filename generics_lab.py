"""GENERICS LAB -- learn by fixing. Every exercise below is DELIBERATELY BROKEN.

HOW TO RUN
    uv run mypy --strict generics_lab.py

HOW TO READ THE OUTPUT
    generics_lab.py:42: error: <message>  [error-code]
                    ^^         ^^^^^^^^^   ^^^^^^^^^^^^
                    line       what broke  the RULE that broke

HOW TO DEBUG  (your single most useful tool)
    reveal_type(x)      -> mypy prints what IT thinks x is. No import needed.
                           Delete these when done; they are notes, not errors.
    assert_type(x, T)   -> mypy ERRORS unless x is EXACTLY T. Needs an import.

    Neither does anything at runtime. `python generics_lab.py` will NOT show you
    types -- only mypy will. That is the whole point: these are compile-time tools.

THE LOOP
    1. run mypy          2. read the FIRST error only
    3. fix it            4. run again -- error count should drop by one

SCOREBOARD -- expected errors per exercise: 1,1,2,1,1,1,1   (8 total)
Work top to bottom. Each fix is independent of the others.
"""

from typing import Any, Callable, assert_type
from pydantic import BaseModel, TypeAdapter


class ReadInput(BaseModel):
    file_path: str
    offset: int = 0


class WriteInput(BaseModel):
    dest: str


class ReadOutput(BaseModel):
    content: str
    line_count: int


# ==============================================================================
# EXERCISE 1 -- a bare generic is NOT "no type", it is Any
# ==============================================================================
# EXPECT: error: Missing type arguments for generic type "list"   [type-arg]
#
# WHY:  `list` alone means `list[Any]`. It IS a hint -- an unfinished one.
#       The element type was thrown away, so xs[0] can do anything.
#
# DEBUG: uncomment the reveal_type -> mypy says "Any". That Any is the bug.
#
# FIX:  give the hole a filling:  xs: list[str]
# ------------------------------------------------------------------------------

def ex1_total_length(xs: list) -> int:
    # reveal_type(xs[0])
    return sum(len(x) for x in xs)


# ==============================================================================
# EXERCISE 2 -- Any DESTROYS information, T PRESERVES it
# ==============================================================================
# EXPECT: error: Expression is of type "Any", not "str"   [assert-type]
#
# WHY:  first_any returns Any, so the checker knows nothing about the result.
#       assert_type demands EXACT identity, so it catches the collapse.
#       An annotation would NOT catch it -- `x: str = first_any(...)` passes,
#       because Any is assignable to everything. That is why we assert here.
#
# DEBUG: reveal_type(first_any(["a"])) -> "Any"
#        reveal_type(first_t(["a"]))   -> "str"    <- compare these two
#
# FIX:  call first_t instead of first_any. Do NOT change the assert_type.
# ------------------------------------------------------------------------------

def first_any(items: list[Any]) -> Any:
    return items[0]


def first_t[T](items: list[T]) -> T:
    """for SOME type T: take a list of T, hand back one T."""
    return items[0]


def ex2() -> None:
    got = first_any(["alice", "bob"])
    assert_type(got, str)


# ==============================================================================
# EXERCISE 3 -- an UNBOUNDED T gives you nothing to call
# ==============================================================================
# EXPECT: 2 errors, both on the return line --
#   error: Returning Any from function declared to return "str"  [no-any-return]
#   error: "M" has no attribute "model_dump_json"   [attr-defined]
#
# WHY:  T with no bound could be filled by int, None, object -- anything.
#       So inside the body you may only do what works on EVERY type.
#       A BOUND is a promise: "whatever fills M is at least a BaseModel",
#       which unlocks BaseModel's API inside the function.
#
# DEBUG: reveal_type(model) -> "M". Ask yourself: what is M guaranteed to be?
#
# FIX:  def ex3_dump[M: BaseModel](model: M) -> str:
# ------------------------------------------------------------------------------

def ex3_dump[M](model: M) -> str:
    return model.model_dump_json()


# ==============================================================================
# EXERCISE 4 -- a class parameter is remembered by the INSTANCE
# ==============================================================================
# EXPECT: error: Argument 1 to "replace" of "Box" has incompatible type "int";
#                expected "str"   [arg-type]
#
# WHY:  Box("hello") binds T=str AT CONSTRUCTION. That box is a Box[str] for
#       its whole life -- .get() returns str, .replace() demands str.
#       Unlike a function, where T is refilled on every call.
#
# DEBUG: reveal_type(b) -> "Box[str]". The [str] is the memory.
#
# FIX:  pass a str to replace(). The class is correct; the CALL is wrong.
# ------------------------------------------------------------------------------

class Box[T]:
    def __init__(self, value: T) -> None:
        self._value = value

    def get(self) -> T:
        return self._value

    def replace(self, value: T) -> "Box[T]":
        return Box(value)


def ex4() -> None:
    b = Box("hello")
    # reveal_type(b)
    b.replace(42)


# ==============================================================================
# EXERCISE 5 -- CONTRAVARIANCE: the resource_keys bug from contracts.py
# ==============================================================================
# EXPECT: error: Incompatible types in assignment (expression has type
#         "Callable[[ReadInput], tuple[str, ...]]", variable has type
#         "Callable[[BaseModel], tuple[str, ...]]")   [assignment]
#
# WHY:  A slot declared `Callable[[BaseModel], ...]` promises: "you may call me
#       with ANY BaseModel." read_keys breaks that promise -- hand it a
#       WriteInput and it explodes on .file_path. So it is NOT a valid filling.
#       Function PARAMETERS flip the direction (contravariance): a narrower
#       parameter makes a function LESS usable, not more.
#
# DEBUG: reveal_type(read_keys) -> "def (ReadInput) -> tuple[str, ...]"
#        then read the declared slot type. Are they the same? Which is wider?
#
# FIX:  the slot must name the tool's own args type:
#           keys_slot: Callable[[ReadInput], tuple[str, ...]] = read_keys
#       In contracts.py this is why the field needs ArgsT, not BaseModel.
# ------------------------------------------------------------------------------

def read_keys(args: ReadInput) -> tuple[str, ...]:
    return (f"fs:{args.file_path}:read",)


keys_slot: Callable[[BaseModel], tuple[str, ...]] = read_keys


# ==============================================================================
# EXERCISE 6 -- INVARIANCE: Spec[A] is not a subtype of Spec[B]
# ==============================================================================
# EXPECT: error: Argument 1 to "ex6_describe" has incompatible type
#         "MiniSpec[ReadInput]"; expected "MiniSpec[BaseModel]"   [arg-type]
#
# WHY:  ArgsT appears BOTH as output (type[ArgsT]) and as input
#       (Callable[[ArgsT], ...]). A parameter used in both directions is
#       INVARIANT: no parameterisation substitutes for any other, even a wider
#       one. This is exactly why `ToolSpec[BaseModel, object]` does not work as
#       a catch-all in contracts.py.
#
# DEBUG: comment out `keys` in MiniSpec and re-run. The error disappears --
#        because ArgsT is then output-only. Put it back. That line IS the cause.
#
# FIX:  make the FUNCTION generic instead of widening the parameter:
#           def ex6_describe[A: BaseModel](spec: MiniSpec[A]) -> str:
# ------------------------------------------------------------------------------

class MiniSpec[ArgsT: BaseModel]:
    def __init__(self, model: type[ArgsT],
                 keys: Callable[[ArgsT], tuple[str, ...]]) -> None:
        self.model = model
        self.keys = keys


def ex6_describe(spec: MiniSpec[BaseModel]) -> str:
    return spec.model.__name__


def ex6() -> None:
    read_spec = MiniSpec(ReadInput, read_keys)
    # reveal_type(read_spec)
    ex6_describe(read_spec)


# ==============================================================================
# EXERCISE 7 -- ERASURE: a type parameter cannot validate anything at runtime
# ==============================================================================
# EXPECT: error: Returning Any from function declared to return "O"
#                [no-any-return]
#
# WHY:  At runtime the parameter is GONE -- Box[str] and Box[int] are literally
#       one class object. So a type parameter can never validate data; only a
#       TypeAdapter can, because it carries a concrete type as DATA.
#
#       Here the adapter is TypeAdapter[Any], so validate_python returns Any,
#       and the `-> O` annotation would silently LAUNDER that Any into O.
#       [no-any-return] is the --strict guard that catches exactly this. It is
#       the same bug that was in contracts.py: TypeAdapter[Any] + a typed slot.
#
# DEBUG: reveal_type(self.adapter.validate_python(raw))
#           TypeAdapter[Any] -> "Any"   (checker gave up)
#           TypeAdapter[O]   -> "O"     (still tracking)
#
# FIX:  adapter: TypeAdapter[O]
#
# THEN TRY: uncomment the print. New error: "O" has no attribute "line_count".
#       Why? O is UNBOUNDED, so nothing is callable on it. That is correct and
#       intentional -- read the output_adapter docstring in contracts.py:342
#       for why OutputT must stay unbounded there.
# ------------------------------------------------------------------------------

class MiniResult[O]:
    def __init__(self, adapter: TypeAdapter[Any]) -> None:
        self.adapter = adapter

    def build(self, raw: object) -> O:
        value = self.adapter.validate_python(raw)
        # print(value.line_count)
        return value


def ex7() -> None:
    r: MiniResult[ReadOutput] = MiniResult(TypeAdapter(ReadOutput))
    out = r.build({"content": "x", "line_count": 1})
    assert_type(out, ReadOutput)     # passes -- build's SIGNATURE promises O
