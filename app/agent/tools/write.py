"""Write -- P0 · fs.write. Step 7 · Phase 06 + Tool Catalog §P0.

Default target decision: ASK, with a CLEARER warning than Edit when the target
already exists -- Edit changes part of a file, Write replaces all of it.
"""

# Shares preconditions, atomicity and locking with edit.py -- read those notes.

import hashlib
from pathlib import Path
from typing import Literal

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
from app.agent.tools.edit import DiffHunk, _hunks

MAX_WRITE_BYTES = 8 * 1024 * 1024

# A model that emits a shortened version with "... rest unchanged ..." in the middle
# destroys the file and the call SUCCEEDS. Refuse these on a file that already exists.
ELISION_MARKERS = (
    "... rest unchanged ...", "// ... rest of", "# ... rest of",
    "<!-- ... -->", "... existing code ...", "# ... (unchanged)",
)


# ==============================================================================
# 1 · Input / output
# ==============================================================================

class WriteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    file_path: str = Field(min_length=1, description="Path to write, inside the workspace.")
    content: str = Field(
        description="The COMPLETE new contents of the file. Never an excerpt."
    )


class WriteOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The create/update discriminator is what lets the UI warn correctly -- and it is
    # also what makes risk_level vary, see §2.
    operation: Literal["create", "update"]
    path: str
    content: str
    original_content: str | None = None
    hunks: list[DiffHunk] = Field(default_factory=list)
    sha256_before: str | None = None
    sha256_after: str


_RECEIPTS_KEY = "write_receipts"


def _receipt_key(path: Path, content: str) -> str:
    """Keyed on (target, content hash) -- the same reasoning as edit.py: on a replay the
    expected-before hash is already gone, so it cannot be part of the lookup."""
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return f"{path}:{digest}"


def _receipts(ctx: ToolRuntimeContext) -> dict[str, WriteOutput]:
    store = ctx.turn.extras.setdefault(_RECEIPTS_KEY, {})
    return store if isinstance(store, dict) else {}


def _completed_replay(ctx: ToolRuntimeContext, path: Path, content: str) -> WriteOutput | None:
    """NEVER overwrite changed content on a retry. The receipt only counts when what is
    on disk right now is exactly what this call already wrote -- if the file moved, the
    retry is not a retry, it is a second write against a file somebody else changed,
    and it goes back through require_fresh_read instead."""
    receipt = _receipts(ctx).get(_receipt_key(path, content))
    if receipt is None:
        return None
    try:
        current = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None
    return receipt if current == receipt.sha256_after else None


class WriteTool(BaseTool[WriteArgs, WriteOutput]):

    spec = ToolSpec[WriteArgs, WriteOutput](
        name="write_file",
        version="1.0.0",
        description=(
            "Write a file, replacing ALL of its contents. content must be the complete "
            "file -- an excerpt or an '... unchanged ...' placeholder destroys whatever "
            "it omits. Overwriting an existing file requires reading it first in this "
            "turn. Prefer edit_file for partial changes."
        ),
        input_model=WriteArgs,
        output_adapter=TypeAdapter(WriteOutput),
        category=ToolCategory.FILESYSTEM,
        side_effect=SideEffect.WORKSPACE_WRITE,
        # The DECLARED level is the ceiling for a new file; permission_facts raises it
        # once the target exists. See §2.
        risk_level=RiskLevel.MEDIUM,
        capabilities=frozenset({"fs.write"}),
        default_permission=Decision.ASK,
        concurrency=ConcurrencyClass.SERIAL_WORKSPACE,
        # Parent creation is a separate intent: take a directory-level lock too, or two
        # concurrent creates race on mkdir.
        resource_keys=lambda args: (
            f"fs:{args.file_path}:write",
            f"fs:{args.file_path.rsplit('/', 1)[0]}:mkdir",
        ),
        timeout=TimeoutPolicy(default_s=15.0, max_s=30.0),
        interrupt_behavior=InterruptBehavior.FINISH,
        idempotency=Idempotency.DEDUPLICATED,
        max_inline_result_bytes=64 * 1024,
        aliases=("Write",),
    )

    # ==========================================================================
    # 3 · Validation
    # ==========================================================================

    async def validate_semantics(self, args: WriteArgs, ctx: ToolRuntimeContext) -> None:
        path = confine(ctx, args.file_path)

        # A completed replay is not an error -- see edit.py. The file already holds
        # exactly what this call would write.
        if _completed_replay(ctx, path, args.content) is not None:
            return

        if len(args.content.encode("utf-8")) > MAX_WRITE_BYTES:
            raise ToolSemanticError(
                f"content is over the {MAX_WRITE_BYTES // 1024} KB write cap.",
                remedy="Write a smaller file, or split it.",
            )
        if is_secret_path(path):
            raise ToolSemanticError(
                f"{path.name} is a secret-bearing file.",
                remedy="Ask the user to write it themselves.", path=str(path),
            )

        # PARENT CONTAINMENT -- creating a directory tree is also a write, so the
        # parent has to clear the same boundary as the file.
        confine(ctx, str(path.parent))

        if not path.exists():
            return                              # a create; nothing more to check

        assert_regular_file(path, verb="written")   # never a special device target

        elision = next((m for m in ELISION_MARKERS if m in args.content), None)
        if elision is not None:
            raise ToolSemanticError(
                f"content contains the placeholder {elision!r} but {path.name} already exists.",
                remedy="Send the complete file, or use edit_file to change one part of it.",
            )

        # An existing file requires a COMPLETE FRESH READ and a matching hash.
        require_fresh_read(ctx, path)

    # ==========================================================================
    # 2 · Risk is not constant
    # ==========================================================================

    async def permission_facts(self, args: WriteArgs, ctx: ToolRuntimeContext) -> PermissionFacts:
        path = confine(ctx, args.file_path)

        if is_protected_path(path, ctx.workspace_root):
            # ELEVATED REVIEW regardless of mode, for files that execute or configure.
            risk = RiskLevel.CRITICAL
        elif path.exists():
            risk = RiskLevel.MEDIUM     # a destructive overwrite of state that mattered
        else:
            risk = RiskLevel.LOW        # trivially reverted, nothing was lost

        return PermissionFacts(
            capabilities=self.spec.capabilities,
            side_effect=self.spec.side_effect,
            risk_level=risk,
            resource_keys=self.spec.resource_keys(args),
            human_summary=self.human_summary(args),
        )

    def human_summary(self, args: WriteArgs) -> str:
        lines = args.content.count("\n") + 1
        return f"Write {args.file_path} ({lines} lines, replacing the entire file)"

    # ==========================================================================
    # 4 · Write mechanics + idempotency
    # ==========================================================================

    async def execute(self, args: WriteArgs, ctx: ToolRuntimeContext) -> WriteOutput:
        path = confine(ctx, args.file_path)
        label = relativise(path, ctx.workspace_root)
        data = args.content.encode("utf-8")
        after_sha = hashlib.sha256(data).hexdigest()

        replay = _completed_replay(ctx, path, args.content)
        if replay is not None:
            return replay

        exists = path.exists()
        before_sha: str | None = None
        original: str | None = None
        mode: int | None = None

        if exists:
            # Re-check after approval, for the same reason edit.py does: the approval
            # was for the file the user SAW, and an ASK suspends the turn.
            current = require_fresh_read(ctx, path)
            before_sha = current.sha256
            original = (await anyio.Path(path).read_bytes()).decode("utf-8", errors="replace")
            mode = path.stat().st_mode & 0o7777

        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, data, mode=mode)

        file_state(ctx).record_read(fingerprint(path, data), complete=True)

        out = WriteOutput(
            operation="update" if exists else "create",
            path=label, content=args.content, original_content=original,
            hunks=_hunks(original or "", args.content, label) if exists else [],
            sha256_before=before_sha, sha256_after=after_sha,
        )
        _receipts(ctx)[_receipt_key(path, args.content)] = out
        return out


# ==============================================================================
# Gate  ->  tests/agent/test_preconditions.py
# ==============================================================================

# NOTE ->> new path -> create, risk LOW.
# NOTE ->> existing path without a fresh read -> refused.
# NOTE ->> existing path with a stale hash -> refused, nothing written.
# NOTE ->> parent directory created only inside the project root.
# NOTE ->> a settings / CI / hook path -> asks even in ACCEPT_EDITS.
# NOTE ->> replayed call does not write twice.
# NOTE ->> partial write interrupted by a crash leaves the ORIGINAL file intact (atomicity).
