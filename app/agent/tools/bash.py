"""Bash -- P0 · process.spawn. Step 12 · Phase 11b + Tool Catalog §P0.

LAST, deliberately. Everything before this was safe BY STRUCTURE -- confined to the
project, read-before-edit, no network. bash cannot be made safe by structure, so it
is the one tool that genuinely needs the permission gate, and shipping the gate on
a tool that can `rm -rf` is the wrong place to discover a bug in it.

Default target decision: command-specific policy; otherwise ASK.
"""

# STATUS ->> The classifier and the process lifecycle below are complete and tested on
# STATUS ->> their own terms. What is NOT yet real is Step 8's PermissionPolicy -- this
# STATUS ->> tool only ever REPORTS facts (permission_facts), so nothing here decides
# STATUS ->> anything. Registering it is gated on that policy landing; build_registry()
# STATUS ->> in __init__.py leaves it out until then, and says so.

import os
import shlex
import signal
import subprocess
import sys
from dataclasses import dataclass

import anyio
import anyio.to_thread
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.agent.base import BaseTool, ToolSemanticError
from app.agent.contracts import (
    ConcurrencyClass, Decision, Idempotency, InterruptBehavior, PermissionFacts,
    RiskLevel, SideEffect, TimeoutPolicy, ToolCategory, ToolRuntimeContext, ToolSpec,
)

MAX_OUTPUT_BYTES = 64 * 1024
INLINE_OUTPUT_CHARS = 30_000
DEFAULT_TIMEOUT_MS = 120_000
MAX_TIMEOUT_MS = 600_000


# ==============================================================================
# 1 · Input / output
# ==============================================================================

class BashArgs(BaseModel):
    """INTERNAL FIELDS ARE EXCLUDED FROM THE MODEL SCHEMA. If the model can name a
    field, the model can set it -- a "dangerously skip sandbox" flag in the public
    schema is a permission bypass with a docstring. Sandbox overrides, simulated-edit
    data and approval metadata travel in ToolRuntimeContext, never here."""

    model_config = ConfigDict(extra="forbid", strict=True)

    command: str = Field(min_length=1)
    timeout: int | None = Field(
        default=None, ge=1, le=MAX_TIMEOUT_MS, description="Milliseconds.",
    )
    description: str = Field(default="", description="What this command does, for the user.")
    run_in_background: bool = False


class BashOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stdout: str
    stderr: str
    exit_code: int | None = None
    interrupted: bool = False
    timed_out: bool = False
    truncated_bytes: int = 0
    background_task_id: str | None = None


# ==============================================================================
# 2 · Permission  --  parse, never pattern-match
# ==============================================================================

# A substring or prefix check on the raw string is defeated by ; && || | $(...)
# backticks, newlines and >. So the string is SPLIT into segments and every segment
# is classified on its own; the command is only as safe as its most dangerous part.
_SEPARATORS = (";", "&&", "||", "|", "\n", "&")

# Proven read-only VERBS. Conservative on purpose. `git` is NOT here, because of
# `git push` -- the verb alone does not settle it.
READ_ONLY_VERBS = frozenset({
    "ls", "cat", "pwd", "echo", "head", "tail", "wc", "file", "stat", "date",
    "whoami", "hostname", "uname", "which", "type", "env", "printenv", "df", "du",
    "grep", "rg", "find", "sort", "uniq", "diff", "basename", "dirname", "realpath",
    "tree", "id", "ps",
})

# Unbypassable default deny-list. It does not stop a determined attacker; it stops an
# ACCIDENT, which is far more common.
DENIED_VERBS = frozenset({
    "shutdown", "reboot", "halt", "poweroff", "mkfs", "fdisk", "dd", "userdel",
    "passwd", "visudo", "chpasswd",
})

# Verbs whose ordinary use is irreversible local loss.
DESTRUCTIVE_VERBS = frozenset({"rm", "rmdir", "shred", "truncate", "mv", "chmod", "chown"})

# Verbs that leave the machine. The channel prompt injection needs to exfiltrate.
NETWORK_VERBS = frozenset({"curl", "wget", "ssh", "scp", "rsync", "nc", "ftp", "telnet"})

# Verbs that touch state somebody else owns, or a shared index.
GIT_WRITE_SUBCOMMANDS = frozenset({
    "push", "commit", "merge", "rebase", "reset", "clean", "checkout", "switch",
    "cherry-pick", "revert", "tag", "branch", "stash", "am", "apply",
})

# An ALLOW-list, not the complement of the write-list. `git <something we have never
# heard of>` is not read-only just because it is absent from the list above -- new
# subcommands and aliases arrive all the time, and a default of "safe" means every one
# of them is auto-allowed on the day it ships.
GIT_READ_SUBCOMMANDS = frozenset({
    "status", "log", "diff", "show", "blame", "describe", "rev-parse", "ls-files",
    "ls-tree", "cat-file", "shortlog", "reflog", "config",
})


_RISK_ORDER = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2, RiskLevel.CRITICAL: 3}


def _worst(*levels: RiskLevel) -> RiskLevel:
    return max(levels, key=_RISK_ORDER.__getitem__)


@dataclass(frozen=True, slots=True)
class Segment:
    """One parsed command in a compound line. `ls && rm -rf /` is not an `ls`."""

    verb: str
    argv: tuple[str, ...]
    redirects: bool


@dataclass(frozen=True, slots=True)
class CommandFacts:
    segments: tuple[Segment, ...]
    read_only: bool
    side_effect: SideEffect
    risk: RiskLevel
    denied: str | None = None


def split_segments(command: str) -> list[list[str]]:
    """Split on every shell separator, then lex each piece.

    Raises ValueError when the line cannot be lexed -- UNPARSABLE MEANS UNKNOWN, and
    unknown asks or denies. It never falls through to allow.
    """
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError as exc:
        raise ValueError(f"cannot parse command: {exc}") from None

    segments: list[list[str]] = [[]]
    for token in tokens:
        if token in _SEPARATORS or set(token) <= {"&", "|", ";"} and token:
            segments.append([])
        else:
            segments[-1].append(token)
    return [seg for seg in segments if seg]


def _substitutes(command: str) -> bool:
    """`$(...)` and backticks run a nested command whose text we never classified."""
    return "$(" in command or "`" in command


def classify(command: str) -> CommandFacts:
    """Apply, in order: hard deny-list, destructive semantics, network effects, then
    read-only proof. THE ORDER IS THE SECURITY PROPERTY -- the most dangerous verdict
    a segment can earn is the verdict for the whole line."""
    try:
        raw_segments = split_segments(command)
    except ValueError as exc:
        return CommandFacts(
            segments=(), read_only=False, side_effect=SideEffect.PROCESS,
            risk=RiskLevel.CRITICAL, denied=str(exc),
        )

    if not raw_segments:
        return CommandFacts((), False, SideEffect.PROCESS, RiskLevel.CRITICAL,
                            denied="empty command")

    segments: list[Segment] = []
    side_effect = SideEffect.NONE
    risk = RiskLevel.LOW
    read_only = True
    denied: str | None = None

    # A command substitution is a whole command we did not see. Treat the line as
    # unknown rather than trusting the verbs around it.
    if _substitutes(command):
        read_only = False
        side_effect = SideEffect.PROCESS
        risk = RiskLevel.HIGH

    for argv in raw_segments:
        verb = os.path.basename(argv[0]).removesuffix(".exe")
        redirects = any(tok in (">", ">>", "<", "2>", "&>") for tok in argv)
        segments.append(Segment(verb=verb, argv=tuple(argv), redirects=redirects))

        if verb in DENIED_VERBS:
            denied = f"{verb} is on the unbypassable deny-list"

        if verb in DESTRUCTIVE_VERBS:
            read_only = False
            side_effect = SideEffect.DESTRUCTIVE
            risk = RiskLevel.HIGH
        elif verb in NETWORK_VERBS:
            read_only = False
            if side_effect is not SideEffect.DESTRUCTIVE:
                side_effect = SideEffect.EXTERNAL_WRITE
            risk = _worst(risk, RiskLevel.HIGH)
        elif verb == "git":
            # `git` is not read-only as a verb; the SUBCOMMAND settles it.
            sub = next((a for a in argv[1:] if not a.startswith("-")), "")
            if sub in GIT_WRITE_SUBCOMMANDS:
                read_only = False
                if side_effect is SideEffect.NONE:
                    side_effect = SideEffect.EXTERNAL_WRITE if sub == "push" else SideEffect.WORKSPACE_WRITE
                risk = _worst(risk, RiskLevel.MEDIUM)
            elif sub not in GIT_READ_SUBCOMMANDS:
                read_only = False
                if side_effect is SideEffect.NONE:
                    side_effect = SideEffect.PROCESS
                risk = _worst(risk, RiskLevel.MEDIUM)
        elif verb not in READ_ONLY_VERBS:
            # Unknown verb: not provably read-only, so it is not read-only.
            read_only = False
            if side_effect is SideEffect.NONE:
                side_effect = SideEffect.PROCESS
            risk = _worst(risk, RiskLevel.MEDIUM)

        if redirects:
            # A redirect writes a file no matter how innocent the verb is.
            read_only = False
            if side_effect in (SideEffect.NONE, SideEffect.PROCESS):
                side_effect = SideEffect.WORKSPACE_WRITE
            risk = _worst(risk, RiskLevel.MEDIUM)

    if read_only and side_effect is SideEffect.NONE:
        risk = RiskLevel.LOW
    if denied is not None:
        risk = RiskLevel.CRITICAL

    return CommandFacts(tuple(segments), read_only, side_effect, risk, denied)


# ==============================================================================
# 3 · Process lifecycle
# ==============================================================================

# Bounded ENVIRONMENT -- allowlist, do not inherit os.environ wholesale. It carries
# API keys straight into anything the model runs.
ENV_ALLOWLIST = (
    "PATH", "HOME", "USER", "USERPROFILE", "LANG", "LC_ALL", "TZ", "TERM",
    "SystemRoot", "COMSPEC", "PATHEXT", "TEMP", "TMP", "NUMBER_OF_PROCESSORS",
)


def _child_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in ENV_ALLOWLIST}
    env["SERA_AGENT"] = "1"
    return env


def _spawn(command: str, cwd: str) -> "subprocess.Popen[bytes]":
    """PROCESS GROUP, so a timeout can kill the GROUP rather than just the shell.

    A killed `npm test` that orphans node processes leaves them holding ports and file
    locks, and the next run fails for a reason that looks entirely unrelated. POSIX
    gets start_new_session; Windows has no process groups in that sense, so it gets
    CREATE_NEW_PROCESS_GROUP and taskkill /T on the way out.

    The two branches are spelled out rather than splatting a **kwargs dict: an untyped
    dict defeats Popen's overloads, and the one that gets picked then decides whether
    stdout is bytes or str for every caller downstream.
    """
    if sys.platform == "win32":
        return subprocess.Popen(
            command, shell=True, cwd=cwd, env=_child_env(),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    return subprocess.Popen(
        command, shell=True, cwd=cwd, env=_child_env(),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True,
    )


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    """Coroutine cancellation is NOT enough for a subprocess."""
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=10)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        proc.kill()


def _run(command: str, cwd: str, timeout_s: float) -> tuple[bytes, bytes, int | None, bool]:
    """Blocking; the caller threads it. Returns (stdout, stderr, code, timed_out)."""
    proc = _spawn(command, cwd)
    try:
        out, err = proc.communicate(timeout=timeout_s)
        return out, err, proc.returncode, False
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        # Collect whatever the group produced before it died -- a timeout with no
        # output at all is the least useful possible error.
        try:
            out, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            out, err = b"", b""
        return out, err, None, True
    except BaseException:
        _kill_tree(proc)
        raise


def _bound(raw: bytes) -> tuple[str, int]:
    text = raw.decode("utf-8", errors="replace")
    if len(raw) <= MAX_OUTPUT_BYTES:
        return text, 0
    kept = raw[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
    return kept, len(raw) - MAX_OUTPUT_BYTES


class BashTool(BaseTool[BashArgs, BashOutput]):

    spec = ToolSpec[BashArgs, BashOutput](
        name="bash",
        version="1.0.0",
        description=(
            "Run a shell command in the workspace. Prefer the dedicated read, glob, "
            "grep, edit and write tools where one fits -- they are cheaper and do not "
            "need approval. timeout is in milliseconds."
        ),
        input_model=BashArgs,
        output_adapter=TypeAdapter(BashOutput),
        category=ToolCategory.SHELL,
        side_effect=SideEffect.PROCESS,
        risk_level=RiskLevel.HIGH,
        capabilities=frozenset({"process.spawn"}),
        default_permission=Decision.ASK,
        # ONLY proven read-only commands may overlap; permission_facts narrows the keys
        # per-invocation. The class stays serial because the class cannot see the args.
        concurrency=ConcurrencyClass.SERIAL_WORKSPACE,
        resource_keys=lambda args: _keys_for(args.command),
        timeout=TimeoutPolicy(
            default_s=DEFAULT_TIMEOUT_MS / 1000, max_s=MAX_TIMEOUT_MS / 1000,
            idle_s=60.0,
        ),
        interrupt_behavior=InterruptBehavior.CANCEL,
        # NEVER auto-retry after ambiguous process loss: the command may have completed
        # and the connection dropped. Retrying `git push` is noise; retrying
        # `rm -rf build && make install` twice is not.
        idempotency=Idempotency.NON_IDEMPOTENT,
        max_inline_result_bytes=INLINE_OUTPUT_CHARS,
        aliases=("Bash",),
    )

    async def validate_semantics(self, args: BashArgs, ctx: ToolRuntimeContext) -> None:
        facts = classify(args.command)
        if facts.denied is not None:
            raise ToolSemanticError(
                facts.denied,
                remedy="Rewrite the command, or ask the user to run it themselves.",
                command=args.command,
            )
        if args.run_in_background:
            raise ToolSemanticError(
                "background execution is not implemented yet.",
                remedy="Run the command in the foreground with a timeout.",
            )

    async def permission_facts(self, args: BashArgs, ctx: ToolRuntimeContext) -> PermissionFacts:
        """This is why permission_key took ARGS back in Step 2. bash(ls) is
        side_effect=none; bash(rm -rf) is destructive. One spec cannot say both."""
        facts = classify(args.command)
        return PermissionFacts(
            capabilities=self.spec.capabilities,
            side_effect=facts.side_effect,
            risk_level=facts.risk,
            # bash(git status) must NOT grant bash(rm). Allow-list entries are permission
            # KEYS carrying the exact command, never the tool name.
            resource_keys=_keys_for(args.command),
            human_summary=self.human_summary(args),
        )

    def human_summary(self, args: BashArgs) -> str:
        if args.description:
            return f"Run: {args.command}  ({args.description})"
        return f"Run: {args.command}"

    async def execute(self, args: BashArgs, ctx: ToolRuntimeContext) -> BashOutput:
        requested_s = args.timeout / 1000 if args.timeout is not None else None
        budget = ctx.turn.budget_for(self.spec, requested_s)

        out, err, code, timed_out = await anyio.to_thread.run_sync(
            _run, args.command, str(ctx.workspace_root), budget,
            abandon_on_cancel=False,
        )

        stdout, dropped_out = _bound(out)
        stderr, dropped_err = _bound(err)

        return BashOutput(
            stdout=stdout, stderr=stderr, exit_code=code, timed_out=timed_out,
            truncated_bytes=dropped_out + dropped_err,
        )


def _keys_for(command: str) -> tuple[str, ...]:
    """Lock keys from the PARSED command. `git` gets a semantic lock of its own --
    two concurrent git commands corrupt the index."""
    facts = classify(command)
    keys = [f"bash({command})"]
    if facts.read_only:
        return tuple(keys)
    keys.append("workspace:write")
    if any(seg.verb == "git" for seg in facts.segments):
        keys.append("repo:git-write")
    return tuple(keys)


# ==============================================================================
# Gate  ->  tests/agent/test_tools_bash.py
# ==============================================================================

# NOTE ->> a 10-second timeout leaves ZERO orphan processes, verified per platform.
# NOTE ->> `ls && rm -rf /` is not classified as read-only.
# NOTE ->> an unparsable command denies rather than allowing.
# NOTE ->> bash(git status) approved does not allow bash(rm -rf build).
# NOTE ->> the model cannot set the sandbox-override field (it is not in the schema).
# NOTE ->> the child environment contains no key from the parent allowlist gaps.
# NOTE ->> output over the cap is persisted and referenced, not inlined.
