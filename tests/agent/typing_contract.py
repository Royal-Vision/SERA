"""Static half of the JsonSchema gate. NOT run by the interpreter -- run by mypy.

Every line below that MUST be a type error carries a `# type: ignore[code]`.
Under --strict (which turns on warn_unused_ignores) that inverts into an
assertion: if the guarantee ever regresses and the line stops erroring, the
ignore becomes "unused" and mypy fails. So this file passing CLEAN is the proof
that each mutation is genuinely unspellable.

Each case gets its own function. mypy suppresses cascading errors on a value it
has already flagged, so sharing one variable would silently disarm every case
after the first.
"""

from typing import assert_type

from pydantic import BaseModel, JsonValue

from app.agent.base import JsonSchema, SchemaOf, Tool


class ReadArgs(BaseModel):
    path: str


class WriteArgs(BaseModel):
    path: str
    content: str


# ==============================================================================
# MUST FAIL  --  the ignores are the assertions
# ==============================================================================

def depth_no_any(s: SchemaOf[ReadArgs]) -> None:
    """1. DEPTH. Under dict[str, Any] this was silently fine."""
    s["title"].split()  # type: ignore[union-attr]


def provenance(r: SchemaOf[ReadArgs]) -> None:
    """2. PROVENANCE. Read's schema is not Write's schema."""
    w: SchemaOf[WriteArgs] = r  # type: ignore[assignment]
    del w


def ownership_setitem(s: SchemaOf[ReadArgs]) -> None:
    """3. OWNERSHIP -- the cached copy is shared; no caller may edit it."""
    s["title"] = "hacked"  # type: ignore[index, assignment]


def ownership_delitem(s: SchemaOf[ReadArgs]) -> None:
    del s["title"]  # type: ignore[arg-type]


def ownership_ior(s: SchemaOf[ReadArgs]) -> None:
    s |= {"title": "hacked"}  # type: ignore[arg-type]


# ==============================================================================
# MUST PASS  --  the design has to stay usable, not just safe
# ==============================================================================

def erasure_needs_no_any(r: SchemaOf[ReadArgs], w: SchemaOf[WriteArgs]) -> None:
    """A heterogeneous snapshot never has to reach for Any."""
    erased: JsonSchema = r
    snapshot: dict[str, JsonSchema] = {"read": r, "write": w}
    assert_type(erased.sha256(), str)
    assert_type(snapshot["read"].sha256(), str)


def leaves_are_json(s: SchemaOf[ReadArgs]) -> None:
    assert_type(s["title"], JsonValue)


def build_infers_the_model() -> None:
    assert_type(SchemaOf.of(ReadArgs), SchemaOf[ReadArgs])


def protocol_return_carries_the_model(tool: Tool[ReadArgs, str]) -> None:
    assert_type(tool.json_schema(), SchemaOf[ReadArgs])
