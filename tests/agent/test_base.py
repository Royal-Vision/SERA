"""Runtime half of the JsonSchema gate -- Tool Contract SRS §01.

The static half lives in typing_contract.py and is checked by mypy, not by
pytest: provenance and immutability are compile-time properties, and a test that
only ran the interpreter would report green on a design that had lost both.
test_typing_contract_holds below is the seam between the two.
"""

import subprocess
import sys
from pathlib import Path

import orjson
import pytest
from pydantic import BaseModel, ConfigDict

from app.agent.base import JsonSchema, SchemaOf

REPO_ROOT = Path(__file__).resolve().parents[2]


class ReadArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str
    limit: int | None = None


class WriteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str
    content: str


@pytest.fixture
def schema() -> SchemaOf[ReadArgs]:
    return SchemaOf.of(ReadArgs)


# ==============================================================================
# Build  --  one source, one shape
# ==============================================================================

def test_of_reproduces_pydantic_exactly(schema: SchemaOf[ReadArgs]) -> None:
    """The input model is the ONE schema source. No second definition to drift."""
    assert dict(schema) == ReadArgs.model_json_schema()


def test_of_is_the_erased_type_too(schema: SchemaOf[ReadArgs]) -> None:
    """Widening to JsonSchema is ordinary subtyping, so snapshots need no Any."""
    assert isinstance(schema, JsonSchema)
    assert isinstance(schema, dict)


# ==============================================================================
# Ownership  --  the cached copy is shared (TOOL-013)
# ==============================================================================

@pytest.mark.parametrize("mutate", [
    pytest.param(lambda s: s.__setitem__("title", "hacked"), id="setitem"),
    pytest.param(lambda s: s.__delitem__("title"), id="delitem"),
    pytest.param(lambda s: s.__ior__({"title": "hacked"}), id="ior"),
    pytest.param(lambda s: s.update({"title": "hacked"}), id="update"),
    pytest.param(lambda s: s.setdefault("title", "hacked"), id="setdefault"),
    pytest.param(lambda s: s.pop("title"), id="pop"),
    pytest.param(lambda s: s.popitem(), id="popitem"),
    pytest.param(lambda s: s.clear(), id="clear"),
])
def test_every_mutator_raises(schema: SchemaOf[ReadArgs], mutate) -> None:
    """dict has more ways in than people remember. Each one is closed."""
    before = dict(schema)
    with pytest.raises(TypeError, match="cached and shared"):
        mutate(schema)
    assert dict(schema) == before, "mutation partially landed before raising"


def test_slots_blocks_attribute_stashing(schema: SchemaOf[ReadArgs]) -> None:
    """__slots__ = () closes the other door: no instance __dict__ to hang state on.

    Without it a dict subclass still accepts `schema.note = ...`, and a shared
    cached object grows per-caller state that nothing else can see.
    """
    assert not hasattr(schema, "__dict__")
    with pytest.raises(AttributeError):
        schema.note = "mine"  # type: ignore[attr-defined]


def test_copy_is_the_supported_escape(schema: SchemaOf[ReadArgs]) -> None:
    """Frozen is not a dead end -- callers that must edit take a plain dict."""
    editable = dict(schema)
    editable["title"] = "fine, it is mine now"
    assert schema["title"] == "ReadArgs"


# ==============================================================================
# Identity  --  the hash recorded in the snapshot
# ==============================================================================

def test_sha256_is_stable_across_rebuilds() -> None:
    assert SchemaOf.of(ReadArgs).sha256() == SchemaOf.of(ReadArgs).sha256()


def test_sha256_separates_models() -> None:
    assert SchemaOf.of(ReadArgs).sha256() != SchemaOf.of(WriteArgs).sha256()


def test_sha256_ignores_key_insertion_order() -> None:
    """OPT_SORT_KEYS. Equal schemas must hash equal however they were built, or a
    snapshot diff reports a schema change that never happened."""
    a = JsonSchema({"type": "object", "title": "T"})
    b = JsonSchema({"title": "T", "type": "object"})
    assert a.sha256() == b.sha256()


def test_sha256_tracks_a_real_field_change() -> None:
    class ReadArgsV2(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)
        path: str
        limit: int | None = None
        offset: int = 0

    assert SchemaOf.of(ReadArgs).sha256() != SchemaOf.of(ReadArgsV2).sha256()


# ==============================================================================
# Wire  --  it has to reach the provider without a copy
# ==============================================================================

def test_orjson_serialises_natively(schema: SchemaOf[ReadArgs]) -> None:
    """The reason this is a dict subclass and not a Mapping: no `default=` hook,
    no per-turn copy on a path that re-sends every schema every turn."""
    assert orjson.loads(orjson.dumps(schema)) == ReadArgs.model_json_schema()


def test_survives_a_realistic_provider_payload(schema: SchemaOf[ReadArgs]) -> None:
    payload = {"name": "read", "description": "Read a file.", "input_schema": schema}
    assert orjson.loads(orjson.dumps(payload))["input_schema"]["title"] == "ReadArgs"


# ==============================================================================
# Gates the module header promises
# ==============================================================================

def test_import_pulls_in_neither_langchain_nor_langgraph() -> None:
    """The 400 ms handshake budget. `import langgraph.graph` alone is ~1800 ms."""
    probe = (
        "import sys; import app.agent.base; "
        "print([m for m in sys.modules "
        "if m.split('.')[0] in ('langchain','langgraph','torch','transformers')])"
    )
    out = subprocess.run([sys.executable, "-c", probe], cwd=REPO_ROOT,
                        capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]", f"fast path polluted: {out.stdout}"


def test_typing_contract_holds() -> None:
    """Run mypy on the static half. Its `# type: ignore` comments are assertions:
    under --strict an ignore that stops being needed is itself an error, so this
    fails the moment a mutation or a mismatched schema becomes spellable again.

    Scoped to typing_contract.py so unrelated work-in-progress elsewhere in
    app/agent does not turn this red.
    """
    target = "tests/agent/typing_contract.py"
    out = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", "--explicit-package-bases", target],
        cwd=REPO_ROOT, capture_output=True, text=True,
        env={"MYPYPATH": str(REPO_ROOT), "PATH": "/usr/bin:/bin"},
    )
    offending = [ln for ln in out.stdout.splitlines() if target in ln]
    assert not offending, "typing contract regressed:\n" + "\n".join(offending)
