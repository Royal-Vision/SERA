"""Filesystem primitives shared by read, glob, grep, edit and write.

Everything here is either a GUARDRAIL (pruning, containment, regular-file checks) or
a FINGERPRINT (identity, staleness). Both exist because the same mistake -- trusting a
path string -- shows up in six tools, and fixing it in six places means fixing it in
five.

Import discipline is inherited from base.py: stdlib + pydantic, plus anyio for I/O.
"""

import hashlib
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.agent.base import ToolSemanticError
from app.agent.contracts import ToolRuntimeContext

# ==============================================================================
# 1 · PRUNE_DIRS  --  a Tier-0 guardrail that happens to be the perf story
# ==============================================================================

# .venv on THIS repo holds >40 000 files. Unpruned `**/*.py` is ~40 ms vs several
# seconds -- and worse, site-packages noise crowds the project's own code out of a
# truncated result list. Prune on the way DOWN, never filter on the way out: that is
# also what keeps .git and .env-bearing directories out of results by default.
PRUNE_DIRS = frozenset({
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "dist", "build", ".next", ".tox", ".idea",
    ".vscode", "site-packages", ".eggs", "htmlcov",
})

# Containment is not the whole check -- .env is inside the project and still must not
# be read on a whim. Matched on the BASENAME, case-folded.
SECRET_BASENAMES = frozenset({
    ".env", ".env.local", ".env.production", ".env.development", ".netrc",
    "id_rsa", "id_ed25519", "credentials", ".npmrc", ".pypirc", ".htpasswd",
})
SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".keystore")

# Writing here is a supply-chain event, not an edit. Elevated review regardless of
# mode -- a tool that can write .github/workflows/ under ACCEPT_EDITS is a hole.
PROTECTED_PREFIXES = (
    ".github/workflows", ".github/actions", ".claude", ".git",
    ".vscode", ".devcontainer",
)
PROTECTED_BASENAMES = frozenset({
    "settings.json", "settings.local.json", "pyproject.toml", "uv.lock",
    "dockerfile", "docker-compose.yml", "makefile",
})


def prunable(name: str) -> bool:
    """Directory names the walk must not descend into."""
    return name in PRUNE_DIRS or name.startswith(".")


def is_secret_path(path: Path) -> bool:
    name = path.name.casefold()
    return name in SECRET_BASENAMES or name.endswith(SECRET_SUFFIXES)


def is_protected_path(path: Path, root: Path) -> bool:
    rel = relativise(path, root).replace(os.sep, "/").casefold()
    return (rel.startswith(PROTECTED_PREFIXES)
            or path.name.casefold() in PROTECTED_BASENAMES)


def relativise(path: Path, root: Path) -> str:
    """Shorter, and it stops absolute paths leaking the machine's directory layout
    into a transcript that gets re-sent every turn."""
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


# ==============================================================================
# 2 · Regular-file gate  --  the check that must happen BEFORE the open
# ==============================================================================

def assert_regular_file(path: Path, *, verb: str = "read") -> os.stat_result:
    """Reject device paths, FIFOs and directories WITHOUT blocking on them.

    Opening /dev/zero or a named pipe blocks forever and burns the whole turn budget
    on a call that never returns -- so this stats and refuses on the MODE, before
    anybody reaches for the bytes.
    """
    try:
        st = path.stat()
    except FileNotFoundError:
        raise ToolSemanticError(
            f"{path.name} does not exist.",
            remedy="Check the path, or search for it with glob before reading.",
            path=str(path),
        ) from None
    except OSError as exc:
        raise ToolSemanticError(
            f"cannot stat {path.name}: {exc.strerror or exc}.",
            remedy="Verify the path is reachable and readable.",
            path=str(path),
        ) from None

    if stat.S_ISDIR(st.st_mode):
        raise ToolSemanticError(
            f"{path.name} is a directory, not a file.",
            remedy="Use glob to list its contents.", path=str(path),
        )
    if not stat.S_ISREG(st.st_mode):
        raise ToolSemanticError(
            f"{path.name} is not a regular file (fifo, socket or device).",
            remedy=f"Only regular files can be {verb}.", path=str(path),
        )
    return st


def confine(ctx: ToolRuntimeContext, raw: str) -> Path:
    """CONFINE FIRST, before touching disk. ValueError -> a semantic refusal.

    ctx.resolve_in_project resolves BEFORE checking containment, so a symlink inside
    the project that walks out is caught here rather than followed.
    """
    try:
        return ctx.resolve_in_project(raw)
    except ValueError as exc:
        raise ToolSemanticError(
            str(exc),
            remedy="Paths must stay inside the workspace root.",
            path=raw,
        ) from None


# ==============================================================================
# 3 · Fingerprints  --  identity is (dev, ino), never the path string
# ==============================================================================

@dataclass(frozen=True, slots=True)
class Fingerprint:
    """What "the file has not changed since you read it" actually means.

    identity is st_dev + st_ino rather than the path, because a RENAME must
    invalidate: the same string can name a different file a second later.
    """

    path: str
    identity: tuple[int, int]
    mtime_ns: int
    size: int
    sha256: str

    def matches(self, other: "Fingerprint") -> bool:
        # Cheap check first: size and mtime settle it almost always. The hash is only
        # consulted when those are ambiguous -- same second, same size.
        if self.identity != other.identity:
            return False
        if self.size != other.size:
            return False
        if self.mtime_ns == other.mtime_ns:
            return True
        return self.sha256 == other.sha256


def fingerprint(path: Path, raw: bytes, st: os.stat_result | None = None) -> Fingerprint:
    """Record with the FULL bytes, BEFORE truncation.

    This is the step that fails SILENTLY when it is wrong: edit compares this hash, so
    hashing truncated bytes means the hash never matches and every edit is refused
    with "read the file first" -- immediately after the model read it.
    """
    st = st or path.stat()
    return Fingerprint(
        path=str(path),
        identity=(st.st_dev, st.st_ino),
        mtime_ns=st.st_mtime_ns,
        size=st.st_size,
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def fingerprint_now(path: Path) -> Fingerprint:
    """Re-read from disk. Used for the after-approval recheck and the staleness test."""
    st = assert_regular_file(path)
    return fingerprint(path, path.read_bytes(), st)


# ==============================================================================
# 4 · Per-turn file state  --  read-before-edit lives here
# ==============================================================================

_STATE_KEY = "file_state"


class FileState:
    """Which files this TURN has read, and what they looked like at the time.

    Lives in AgentContext.extras (contracts.py calls that the tracker's home), so it
    dies with the turn: an authorisation to edit is never inherited by the next one.
    """

    __slots__ = ("_reads", "_delivered")

    def __init__(self) -> None:
        # Keyed on identity, not path. A read of a.py followed by a rename to b.py
        # must not authorise an edit of whatever now sits at a.py.
        self._reads: dict[tuple[int, int], Fingerprint] = {}
        # Two different questions, deliberately two maps:
        #   _reads     -- "is an edit of this authorised?"  A write records here too,
        #                 so a second edit in the same turn is not refused as stale by
        #                 the first edit's own change.
        #   _delivered -- "has the model actually SEEN these bytes?"  Only read_file
        #                 records here. Merging the two would let a read that follows a
        #                 write answer "unchanged" for content nobody ever read.
        # Keyed by (identity, offset, limit): the same file read through a DIFFERENT
        # window is a different set of bytes, and answering "unchanged" for a window
        # the model never received would hide content it is about to reason about.
        self._delivered: dict[tuple[tuple[int, int], int, int], Fingerprint] = {}

    def record_read(self, fp: Fingerprint, *, complete: bool) -> None:
        """Only a COMPLETE read authorises an edit. A paged read saw part of the file,
        and an edit applied on that basis is an edit against content never seen."""
        if complete:
            self._reads[fp.identity] = fp

    def prior_read(self, identity: tuple[int, int]) -> Fingerprint | None:
        return self._reads.get(identity)

    def record_delivery(self, fp: Fingerprint, view: tuple[int, int]) -> None:
        """Called only by read_file, with the bytes that went into the model turn.
        `view` is (offset, limit) -- the window those bytes came from."""
        self._delivered[(fp.identity, *view)] = fp

    def delivered(self, identity: tuple[int, int], view: tuple[int, int]) -> Fingerprint | None:
        return self._delivered.get((identity, *view))

    def forget(self, identity: tuple[int, int]) -> None:
        self._reads.pop(identity, None)
        for key in [k for k in self._delivered if k[0] == identity]:
            del self._delivered[key]


def file_state(ctx: ToolRuntimeContext) -> FileState:
    state = ctx.turn.extras.get(_STATE_KEY)
    if not isinstance(state, FileState):
        state = FileState()
        ctx.turn.extras[_STATE_KEY] = state
    return state


def require_fresh_read(ctx: ToolRuntimeContext, path: Path) -> Fingerprint:
    """The two invariants, in the order that makes the message useful.

    Returns the CURRENT fingerprint so the caller can re-check it after approval -- an
    ASK suspends the turn for however long a human takes to read a diff, and the
    approval they gave was for the diff they SAW.
    """
    current = fingerprint_now(path)
    seen = file_state(ctx).prior_read(current.identity)

    if seen is None:
        raise ToolSemanticError(
            f"{path.name} has not been read in this turn.",
            remedy="Read the file first, then re-apply this change.",
            path=str(path),
        )
    if not seen.matches(current):
        raise ToolSemanticError(
            f"{path.name} changed since you read it.",
            remedy="Read the file again and re-apply the change to the new content.",
            path=str(path),
        )
    return current


# ==============================================================================
# 5 · Atomic replace
# ==============================================================================

def atomic_write(path: Path, data: bytes, *, mode: int | None = None) -> None:
    """Temp file in the SAME directory, fsync, os.replace.

    Same directory because os.replace is only atomic within a filesystem -- a temp in
    the system tmpdir becomes a copy across the device boundary, and a crash mid-copy
    leaves a half-written file where an intact original used to be.

    Mode is carried over DELIBERATELY: a 0755 script that comes back 0644 is a broken
    deploy, and nobody will trace it to the edit tool.
    """
    directory = path.parent
    fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def detect_newline(raw: bytes) -> bytes:
    """No implicit line-ending rewrite. A CRLF file stays CRLF -- converting the whole
    file produces a diff of every line and buries the actual change."""
    first, _, _ = raw.partition(b"\n")
    return b"\r\n" if first.endswith(b"\r") else b"\n"


def is_binary(raw: bytes) -> bool:
    """A NUL in the first block is the cheapest reliable signal; scanning the whole
    file costs more and adds nothing."""
    return b"\x00" in raw[:8192]
