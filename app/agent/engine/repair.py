"""Argument repair -- recover from the ways models actually malform tool calls.

Most "tool failures" are not the tool failing. They are the model emitting arguments
the schema rejects, and a naive executor turning that into a dead turn. Empirically the
failure modes cluster into a small, fixable set:

    1. JSON wrapped in markdown fences        ```json { ... } ```
    2. Prose before or after the object       Sure! Here's the call: { ... }
    3. Trailing commas                        {"a": 1,}
    4. Single quotes                          {'a': 1}
    5. Unterminated object (token cutoff)     {"a": 1, "b": "unclo
    6. Stringified scalars                    {"limit": "5"} for an int field
    7. Stringified nested JSON                {"items": "[1,2]"} for a list field
    8. Python literals                        True / False / None instead of true/false/null
    9. Near-miss tool names                   "readfile" / "read-file" / "Read_File"
   10. Near-miss enum values                  "CONTENT" for "content"

Every repair here is conservative and reported. `RepairLog` records what was changed so
the caller can surface it, meter it, and notice when a provider regresses.

Deliberate non-goal: guessing missing REQUIRED values. Inventing a file path is worse
than a clean error, because a clean error is a prompt the model can act on.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from difflib import get_close_matches
from typing import Any

__all__ = ["RepairLog", "repair_json", "coerce_to_schema", "resolve_tool_name"]


@dataclass(slots=True)
class RepairLog:
    """What we had to fix. Empty means the model got it right."""

    repairs: list[str] = field(default_factory=list)

    def note(self, what: str) -> None:
        self.repairs.append(what)

    @property
    def clean(self) -> bool:
        return not self.repairs

    def __bool__(self) -> bool:
        return bool(self.repairs)

    def __str__(self) -> str:
        return "; ".join(self.repairs)


# ──────────────────────────────────────────────────────────────────────────────
# 1. Text -> dict
# ──────────────────────────────────────────────────────────────────────────────

_FENCE = re.compile(r"^\s*```(?:json|python)?\s*|\s*```\s*$", re.IGNORECASE)


def repair_json(raw: str | dict[str, Any], log: RepairLog | None = None) -> dict[str, Any]:
    """Extract a JSON object from whatever the model produced.

    Raises ValueError only when there is genuinely no object to be found.
    """
    log = RepairLog() if log is None else log

    if isinstance(raw, dict):
        return raw

    text = raw.strip()
    if not text:
        return {}

    # Fast path: it is already valid.
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    if "```" in text:
        text = _FENCE.sub("", text).strip()
        log.note("stripped markdown fence")

    obj_text = _first_balanced_object(text)
    if obj_text is None:
        raise ValueError(f"no JSON object found in model output: {raw[:200]!r}")

    if obj_text != text:
        log.note("extracted object from surrounding prose")

    for attempt, fixer in (
        ("as-is", lambda s: s),
        ("removed trailing commas", _strip_trailing_commas),
        ("python literal eval", None),  # handled specially below
    ):
        if fixer is None:
            break
        candidate = fixer(obj_text)
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                if attempt != "as-is":
                    log.note(attempt)
                return parsed
        except json.JSONDecodeError:
            continue

    # Single quotes / True / None -> ast handles Python-dialect JSON safely.
    # ast.literal_eval only evaluates literals; it cannot execute code.
    try:
        parsed = ast.literal_eval(obj_text)
        if isinstance(parsed, dict):
            log.note("parsed as python literal (single quotes / True / None)")
            return parsed
    except (ValueError, SyntaxError):
        pass

    # Last resort: the object was cut off mid-generation. Close it and retry.
    repaired = _close_unterminated(obj_text)
    if repaired is not None:
        try:
            parsed = json.loads(repaired)
            if isinstance(parsed, dict):
                log.note("closed unterminated JSON (output was truncated)")
                return parsed
        except json.JSONDecodeError:
            pass

    raise ValueError(f"could not parse tool arguments: {raw[:200]!r}")


def _first_balanced_object(text: str) -> str | None:
    """Scan for the first brace-balanced object, ignoring braces inside strings."""
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False

    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text[start:]  # unterminated; _close_unterminated may rescue it


def _strip_trailing_commas(text: str) -> str:
    return re.sub(r",\s*([}\]])", r"\1", text)


def _close_unterminated(text: str) -> str | None:
    """Balance a truncated object so it can be parsed.

    Only helps when generation was cut off. Any value that was itself truncated is
    dropped rather than guessed.
    """
    depth = 0
    in_string = False
    escape = False
    last_complete = 0

    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == "," and depth == 1:
            last_complete = i

    if depth <= 0:
        return None
    if not last_complete:
        return None
    return text[:last_complete] + "}" * depth


# ──────────────────────────────────────────────────────────────────────────────
# 2. Type coercion against the target schema
# ──────────────────────────────────────────────────────────────────────────────

_TRUE = {"true", "yes", "y", "1", "on"}
_FALSE = {"false", "no", "n", "0", "off"}


def coerce_to_schema(
    args: dict[str, Any],
    schema: dict[str, Any],
    log: RepairLog | None = None,
) -> dict[str, Any]:
    """Nudge values toward the declared JSON-Schema types.

    Only unambiguous conversions. `"5"` -> `5` is safe; `"maybe"` -> bool is not, so it
    is left alone for Pydantic to reject with a good message.
    """
    log = RepairLog() if log is None else log
    props: dict[str, Any] = schema.get("properties", {})
    if not props:
        return args

    defs: dict[str, Any] = schema.get("$defs", {})
    out: dict[str, Any] = {}
    for key, value in args.items():
        prop = props.get(key)
        if prop is None:
            out[key] = value
            continue
        out[key] = _coerce_one(key, value, _deref(prop, defs), log)
    return out


def _deref(prop: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    """Inline a `$ref` into `$defs`.

    Pydantic renders an Enum/StrEnum field as a `$ref` (often wrapped in `allOf` or
    `anyOf`) rather than an inline `enum`, so without this every enum near-miss --
    "CONTENT" for "content" -- slips past coercion and dies in validation.
    """
    if not defs:
        return prop

    def resolve(node: dict[str, Any]) -> dict[str, Any]:
        ref = node.get("$ref", "")
        if ref.startswith("#/$defs/"):
            return defs.get(ref.removeprefix("#/$defs/"), {})
        return node

    if "$ref" in prop:
        return {**prop, **resolve(prop)}

    for combinator in ("allOf", "anyOf", "oneOf"):
        for branch in prop.get(combinator, []) or []:
            if not isinstance(branch, dict):
                continue
            target = resolve(branch)
            if target.get("enum") or target.get("type"):
                merged = {**target}
                # Keep the outer default/description; take type+enum from the $def.
                for k in ("default", "description"):
                    if k in prop:
                        merged.setdefault(k, prop[k])
                return merged
    return prop


def _coerce_one(key: str, value: Any, prop: dict[str, Any], log: RepairLog) -> Any:
    target = prop.get("type")

    # anyOf / oneOf (typically `X | None`) -- pick the first concrete type.
    if target is None:
        for branch in prop.get("anyOf", []) or prop.get("oneOf", []):
            if branch.get("type") and branch.get("type") != "null":
                target = branch["type"]
                prop = {**branch, "enum": prop.get("enum", branch.get("enum"))}
                break

    enum = prop.get("enum")
    if enum and isinstance(value, str) and value not in enum:
        match = _closest_enum(value, enum)
        if match is not None:
            log.note(f"{key}: {value!r} -> {match!r}")
            return match
        return value

    if target == "integer" and isinstance(value, str):
        try:
            coerced = int(value.strip())
            log.note(f"{key}: string -> integer")
            return coerced
        except ValueError:
            return value

    if target == "number" and isinstance(value, str):
        try:
            coerced = float(value.strip())
            log.note(f"{key}: string -> number")
            return coerced
        except ValueError:
            return value

    if target == "integer" and isinstance(value, float) and value.is_integer():
        log.note(f"{key}: float -> integer")
        return int(value)

    if target == "boolean" and isinstance(value, str):
        low = value.strip().lower()
        if low in _TRUE:
            log.note(f"{key}: string -> true")
            return True
        if low in _FALSE:
            log.note(f"{key}: string -> false")
            return False
        return value

    if target == "array" and isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    log.note(f"{key}: stringified array -> array")
                    return parsed
            except json.JSONDecodeError:
                pass
        # A bare scalar where a list was wanted is a very common model slip.
        log.note(f"{key}: scalar -> single-element array")
        return [value]

    if target == "object" and isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{"):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, dict):
                    log.note(f"{key}: stringified object -> object")
                    return parsed
            except json.JSONDecodeError:
                pass

    if target == "string" and isinstance(value, (int, float, bool)):
        log.note(f"{key}: scalar -> string")
        return str(value)

    return value


def _closest_enum(value: str, enum: list[Any]) -> Any | None:
    """Case-insensitive, then fuzzy. Enum near-misses are extremely common."""
    options = [e for e in enum if isinstance(e, str)]
    lowered = {o.lower(): o for o in options}
    if value.lower() in lowered:
        return lowered[value.lower()]
    normal = {re.sub(r"[-_\s]", "", o.lower()): o for o in options}
    key = re.sub(r"[-_\s]", "", value.lower())
    if key in normal:
        return normal[key]
    close = get_close_matches(value.lower(), list(lowered), n=1, cutoff=0.85)
    return lowered[close[0]] if close else None


# ──────────────────────────────────────────────────────────────────────────────
# 3. Tool-name resolution
# ──────────────────────────────────────────────────────────────────────────────


def resolve_tool_name(
    requested: str,
    known: list[str],
    log: RepairLog | None = None,
) -> str | None:
    """Map a possibly-mangled tool name onto a real one.

    Returns None when there is no confident match -- better a clean "unknown tool,
    here are the real ones" error than silently running something else.
    """
    log = RepairLog() if log is None else log
    if requested in known:
        return requested

    lowered = {k.lower(): k for k in known}
    if requested.lower() in lowered:
        log.note(f"tool name case: {requested!r} -> {lowered[requested.lower()]!r}")
        return lowered[requested.lower()]

    # read-file / ReadFile / read.file all normalise to readfile
    normal = {re.sub(r"[-_.\s]", "", k.lower()): k for k in known}
    key = re.sub(r"[-_.\s]", "", requested.lower())
    if key in normal:
        log.note(f"tool name style: {requested!r} -> {normal[key]!r}")
        return normal[key]

    # Providers sometimes namespace: "functions.read_file", "default_api.read_file"
    if "." in requested:
        tail = requested.rsplit(".", 1)[-1]
        if tail in known:
            log.note(f"stripped namespace: {requested!r} -> {tail!r}")
            return tail

    close = get_close_matches(key, list(normal), n=1, cutoff=0.85)
    if close:
        resolved = normal[close[0]]
        log.note(f"fuzzy tool name: {requested!r} -> {resolved!r}")
        return resolved

    return None
