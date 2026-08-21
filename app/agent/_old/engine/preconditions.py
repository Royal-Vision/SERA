"""Per-file state tracking -- prevents edits against a stale view.

The most damaging tool failure is not an error. It is a *successful* edit applied to a
file the agent last saw three turns ago, silently discarding whatever changed in
between. An error is recoverable; silent data loss is not.

Two invariants, both enforced here:

    1. read-before-edit  -- an agent must have read a file before modifying it
    2. unchanged-since   -- the file must not have changed on disk since that read

Both produce actionable messages, because a message the model can act on is worth far
more than a correct-but-opaque refusal.

State is per-turn, held in `AgentContext.extras`, so nothing leaks between requests.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from app.agent.contracts import AgentContext

_KEY = "_file_state"


@dataclass(slots=True)
class FileSnapshot:
    """What the agent last saw."""

    sha256: str
    size: int
    mtime_ns: int
    lines: int


@dataclass
class FileStateTracker:
    seen: dict[str, FileSnapshot] = field(default_factory=dict)

    def record_read(self, path: Path, content: bytes) -> None:
        stat = path.stat()
        self.seen[str(path)] = FileSnapshot(
            sha256=hashlib.sha256(content).hexdigest(),
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            lines=content.count(b"\n") + 1,
        )

    def forget(self, path: Path) -> None:
        self.seen.pop(str(path), None)

    def check_editable(self, path: Path) -> str | None:
        """Returns None when the edit may proceed, else an actionable message."""
        key = str(path)
        snap = self.seen.get(key)

        if snap is None:
            return (
                f"You must read {path.name} before editing it. "
                f"Call read_file on it first so your edit is based on its current contents."
            )

        try:
            stat = path.stat()
        except FileNotFoundError:
            self.forget(path)
            return f"{path.name} no longer exists. Re-read it or create it with write_file."

        # Cheap check first; hash only when the cheap check is ambiguous.
        if stat.st_size == snap.size and stat.st_mtime_ns == snap.mtime_ns:
            return None

        current = hashlib.sha256(path.read_bytes()).hexdigest()
        if current == snap.sha256:
            # Touched but not modified -- refresh the snapshot and allow.
            snap.size = stat.st_size
            snap.mtime_ns = stat.st_mtime_ns
            return None

        self.forget(path)
        return (
            f"{path.name} changed on disk since you read it. "
            f"Re-read it before editing, or your change would discard that edit."
        )


def tracker_for(ctx: AgentContext) -> FileStateTracker:
    """Per-turn tracker, lazily created."""
    existing = ctx.extras.get(_KEY)
    if isinstance(existing, FileStateTracker):
        return existing
    fresh = FileStateTracker()
    ctx.extras[_KEY] = fresh
    return fresh
