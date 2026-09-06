"""Edit -- P0 · fs.write. Step 7 · Phase 06 + Tool Catalog §P0.

Default target decision: ASK. ACCEPT_EDITS may allow ordinary workspace files;
protected paths stay ask/deny and are BYPASS-immune.

The failure this tool exists to prevent is not an error. It is a SUCCESSFUL edit
applied to a file the agent last saw three turns ago, silently discarding whatever
changed in between. An error is recoverable; silent data loss is not.
"""

# Preconditions come FIRST (_fs.require_fresh_read). The state machine is what makes
# this tool safe to write at all -- the patch logic is the easy half.

import difflib
import hashlib
from pathlib import Path

import anyio
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.agent.base import BaseTool, ToolSemanticError
from app.agent.contracts import (
    ConcurrencyClass, Decision, Idempotency, InterruptBehavior, PermissionFacts,
    RiskLevel, SideEffect, TimeoutPolicy, ToolCategory, ToolRuntimeContext, ToolSpec,
)
from app.agent.tools._fs import (
    assert_regular_file, atomic_write, confine, file_state, fingerprint,
    is_protected_path, is_secret_path, relativise, require_fresh_read,
)

MAX_EDIT_BYTES = 8 * 1024 * 1024


# ==============================================================================
# 1 · Input / output
# ==============================================================================

class EditArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    file_path: str = Field(min_length=1)
    # EXACT STRINGS, never line numbers. Line numbers go stale the moment anything
    # above them changes; read_file hands the model numbers for DISCUSSION and this
    # tool takes strings for MUTATION. That split is deliberate -- see read.py §6.
    old_string: str = Field(description="Exact text to replace, copied from the file.")
    new_string: str = Field(description="Replacement text.")
    replace_all: bool = False


class DiffHunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    header: str
    lines: list[str]


class EditOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    old_string: str
    new_string: str
    original_content: str
    hunks: list[DiffHunk]
    replacements: int
    replace_all: bool
    # Before AND after hashes -- they are what makes the idempotent retry below
    # decidable at all.
    sha256_before: str
    sha256_after: str
    user_modified: bool = False


def _hunks(before: str, after: str, label: str) -> list[DiffHunk]:
    diff = difflib.unified_diff(
        before.splitlines(), after.splitlines(),
        fromfile=f"a/{label}", tofile=f"b/{label}", lineterm="", n=3,
    )
    hunks: list[DiffHunk] = []
    for line in diff:
        if line.startswith("@@"):
            hunks.append(DiffHunk(header=line, lines=[]))
        elif hunks and not line.startswith(("---", "+++")):
            hunks[-1].lines.append(line)
    return hunks


# ==============================================================================
# 6 · Idempotency  --  the receipt cache
# ==============================================================================

_RECEIPTS_KEY = "edit_receipts"


def _receipt_key(path: Path, args: EditArgs) -> str:
    """Keyed on the ARGUMENTS, not on (before, after).

    The obvious key is the before/after hash pair, but on a replay we no longer HAVE
    the expected-before hash -- the first attempt already changed the file, so the
    lookup misses and the retry falls through to a second edit. Keying on the args and
    then checking the recorded after-hash against what is on disk is what actually
    makes "did this already land?" decidable.
    """
    # Length-prefixed rather than delimiter-joined: old_string and new_string are
    # arbitrary file text, so any separator you pick can also occur inside them and
    # two different edits would collide on one key.
    parts = (str(path), args.old_string, args.new_string, str(args.replace_all))
    payload = "".join(f"{len(part)}:{part}" for part in parts)
    return hashlib.sha256(payload.encode()).hexdigest()


def _receipts(ctx: ToolRuntimeContext) -> dict[str, EditOutput]:
    store = ctx.turn.extras.setdefault(_RECEIPTS_KEY, {})
    return store if isinstance(store, dict) else {}


def _completed_replay(ctx: ToolRuntimeContext, path: Path, args: EditArgs) -> EditOutput | None:
    """A receipt for these exact args whose after-hash is what is on disk right now."""
    receipt = _receipts(ctx).get(_receipt_key(path, args))
    if receipt is None:
        return None
    try:
        current = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None
    return receipt if current == receipt.sha256_after else None


class EditTool(BaseTool[EditArgs, EditOutput]):

    spec = ToolSpec[EditArgs, EditOutput](
        name="edit_file",
        version="1.0.0",
        description=(
            "Replace an exact string in a file. You must read the file in this turn "
            "first. old_string must be copied verbatim from what you read and must "
            "match exactly once unless replace_all is set -- an ambiguous match would "
            "otherwise silently edit the wrong occurrence. Never pass line numbers."
        ),
        input_model=EditArgs,
        output_adapter=TypeAdapter(EditOutput),
        category=ToolCategory.FILESYSTEM,
        side_effect=SideEffect.WORKSPACE_WRITE,
        risk_level=RiskLevel.MEDIUM,
        capabilities=frozenset({"fs.write", "fs.read"}),
        default_permission=Decision.ASK,
        # EXCLUSIVE write lock. The executor's batcher must never put a read and a write
        # to the same path in one batch -- that is a correctness bug, not a performance
        # question.
        concurrency=ConcurrencyClass.SERIAL_WORKSPACE,
        resource_keys=lambda args: (f"fs:{args.file_path}:write",),
        timeout=TimeoutPolicy(default_s=15.0, max_s=30.0),
        # FINISH, not CANCEL: cancelling mid-write is worse than finishing. The write
        # itself is a single os.replace, so "finishing" is microseconds.
        interrupt_behavior=InterruptBehavior.FINISH,
        # Deduplicate on (expected-before hash, after hash). After a reconnect the model
        # re-sends, and old_string is usually gone by then -- so the naive path fails a
        # retry that actually succeeded.
        idempotency=Idempotency.DEDUPLICATED,
        max_inline_result_bytes=64 * 1024,
        aliases=("Edit",),
    )

    # ==========================================================================
    # 4 · Validation
    # ==========================================================================

    async def validate_semantics(self, args: EditArgs, ctx: ToolRuntimeContext) -> None:
        path = confine(ctx, args.file_path)

        # A completed replay is not an error. After a reconnect the model re-sends, and
        # by then old_string is gone -- so the occurrence check below would reject a
        # call that actually succeeded. Check the receipt before the invariants.
        if _completed_replay(ctx, path, args) is not None:
            return

        if args.old_string == args.new_string:
            raise ToolSemanticError(
                "old_string and new_string are identical.",
                remedy="Send the text you actually want in the file.",
            )
        if path.suffix == ".ipynb":
            raise ToolSemanticError(
                "notebooks are not editable with edit_file.",
                remedy="Use NotebookEdit, which understands cell structure.",
                path=str(path),
            )
        if is_secret_path(path):
            raise ToolSemanticError(
                f"{path.name} is a secret-bearing file.",
                remedy="Ask the user to change it themselves.", path=str(path),
            )

        st = assert_regular_file(path, verb="edited")
        if st.st_size > MAX_EDIT_BYTES:
            raise ToolSemanticError(
                f"{path.name} is {st.st_size // 1024} KB, over the {MAX_EDIT_BYTES // 1024} KB edit cap.",
                remedy="Edit a smaller file, or ask the user to split it.",
            )

        # THE TWO INVARIANTS. read-before-edit, then unchanged-since-read.
        require_fresh_read(ctx, path)

        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="strict")
        occurrences = text.count(args.old_string)

        if occurrences == 0:
            raise ToolSemanticError(
                f"old_string does not occur in {path.name}.",
                remedy="Copy the text verbatim from the file, including indentation.",
            )
        if occurrences > 1 and not args.replace_all:
            raise ToolSemanticError(
                f"old_string occurs {occurrences} times in {path.name}.",
                remedy="Include more surrounding text to make it unique, or set replace_all.",
                occurrences=occurrences,
            )

    # ==========================================================================
    # 3 · The window nobody closes
    # ==========================================================================

    async def permission_facts(self, args: EditArgs, ctx: ToolRuntimeContext) -> PermissionFacts:
        path = confine(ctx, args.file_path)
        risk = self.spec.risk_level
        if is_protected_path(path, ctx.workspace_root):
            # Elevated review regardless of mode. A tool that can rewrite CI config
            # under ACCEPT_EDITS is a supply-chain hole.
            risk = RiskLevel.CRITICAL
        return PermissionFacts(
            capabilities=self.spec.capabilities,
            side_effect=self.spec.side_effect,
            risk_level=risk,
            resource_keys=self.spec.resource_keys(args),
            human_summary=self.human_summary(args),
        )

    def human_summary(self, args: EditArgs) -> str:
        scope = "every occurrence" if args.replace_all else "one occurrence"
        first = args.old_string.strip().splitlines()[0] if args.old_string.strip() else ""
        return f"Edit {args.file_path} -- replace {scope} of {first[:60]!r}"

    async def execute(self, args: EditArgs, ctx: ToolRuntimeContext) -> EditOutput:
        path = confine(ctx, args.file_path)
        label = relativise(path, ctx.workspace_root)

        # A repeated completed call returns the EXISTING RECEIPT rather than editing
        # twice -- before any write, and before require_fresh_read would object to a
        # file this tool itself changed.
        replay = _completed_replay(ctx, path, args)
        if replay is not None:
            return replay

        # RECHECK AFTER APPROVAL, immediately before the atomic replace. An ASK
        # suspends the turn -- seconds or minutes of a human reading a diff -- and the
        # file can change in that window. Checking only before the prompt means
        # applying a stale patch with a signature on it.
        current = require_fresh_read(ctx, path)

        # No implicit line-ending rewrite: decoding without newline translation and
        # re-encoding round-trips CRLF byte-for-byte, so a str.replace touches only the
        # lines it actually changes rather than producing a diff of every line.
        raw = await anyio.Path(path).read_bytes()
        text = raw.decode("utf-8")

        count = -1 if args.replace_all else 1
        updated = text.replace(args.old_string, args.new_string, count)
        replacements = text.count(args.old_string) if args.replace_all else 1

        after_bytes = updated.encode("utf-8")
        after_sha = hashlib.sha256(after_bytes).hexdigest()

        # Preserve permissions DELIBERATELY -- a mode-0755 script that comes back 0644
        # is a broken deploy, and it will not be traced to the edit tool.
        st = path.stat()
        atomic_write(path, after_bytes, mode=st.st_mode & 0o7777)

        # The edited file is what the model must reason about next, so the recorded
        # read becomes the POST-edit state -- otherwise a second edit in the same turn
        # is refused as "changed since you read it" by this tool's own write.
        fresh = fingerprint(path, after_bytes)
        file_state(ctx).record_read(fresh, complete=True)

        out = EditOutput(
            path=label, old_string=args.old_string, new_string=args.new_string,
            original_content=text, hunks=_hunks(text, updated, label),
            replacements=replacements, replace_all=args.replace_all,
            sha256_before=current.sha256, sha256_after=after_sha,
        )
        _receipts(ctx)[_receipt_key(path, args)] = out
        return out


# ==============================================================================
# Gate  ->  tests/agent/test_preconditions.py
# ==============================================================================

# NOTE ->> read a file -> modify it externally -> edit refused, message says what to do.
# NOTE ->> edit_file on a never-read file -> refused.
# NOTE ->> old_string matching twice without replace_all -> refused, count reported.
# NOTE ->> file changed DURING the approval window -> refused after approval, not applied.
# NOTE ->> file mode and line endings survive an edit.
# NOTE ->> the same call replayed returns the first receipt and edits once.
# NOTE ->> a .ipynb path -> refused, points at NotebookEdit.
