"""Structured logging for the agent. Runtime plumbing -- Step 1 · Phase 01.

Three rules shape every decision in this file:

1. **stderr only, never stdout.** stdout carries NDJSON protocol frames. One stray
   log line there desynchronises the client, and because JSON parsing then fails on
   the *next* frame, the bug surfaces far from its cause.
2. **Logging must not block the event loop.** A rotating file write is disk I/O; done
   inline it stalls every concurrent tool call in the batch. Records go onto a queue
   and a background thread drains it.
3. **50 MB total, oldest deleted first.** A log that grows without bound eventually
   takes the disk with it -- and on this machine that already happened once.
"""

import atexit
import logging
import logging.handlers
import queue
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

# NOTE ->> orjson is a declared dependency, but the fallback keeps this file importable
# NOTE ->> in a bare interpreter -- same pattern as _old/perf.py. ~77% faster than json.
try:
    import orjson

    def _dumps(obj: Any) -> str:
        return orjson.dumps(obj).decode()

    JSON_BACKEND: Final = "orjson"
except ImportError:  # pragma: no cover -- orjson is declared in pyproject
    import json

    def _dumps(obj: Any) -> str:
        return json.dumps(obj, separators=(",", ":"), default=str)

    JSON_BACKEND: Final = "json"


# ==============================================================================
# 1 · The size budget  --  FIFO by rotation
# ==============================================================================

TOTAL_BUDGET_BYTES: Final = 50 * 1024 * 1024
"""Hard ceiling on everything this logger puts on disk."""

BACKUP_COUNT: Final = 4
"""Files kept behind the live one. app.log, app.log.1 ... app.log.4 -- five total."""

MAX_BYTES: Final = TOTAL_BUDGET_BYTES // (BACKUP_COUNT + 1)
"""10 MiB per file. RotatingFileHandler's real footprint is maxBytes * (backupCount + 1),
so this is the division that makes the 50 MB ceiling true rather than aspirational."""

# NOTE ->> RotatingFileHandler IS the FIFO. On overflow it shifts app.log.N -> N+1 and
# NOTE ->> unlinks whatever falls past backupCount -- oldest out, newest in, no cursor to
# NOTE ->> maintain and no sweeper task that can die and let the disk fill anyway.

DEFAULT_LOG_DIR: Final = Path("logs")
LOG_FILENAME: Final = "agent.log"


# ==============================================================================
# 2 · Per-turn context
# ==============================================================================

# NOTE ->> ContextVar, not a parameter threaded through every call: these follow the
# NOTE ->> async task automatically, so concurrent turns cannot bleed ids into each other.
session_id_var: ContextVar[str] = ContextVar("session_id", default="-")
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class ContextFilter(logging.Filter):
    """Stamp every record with the turn it belongs to.

    A Filter rather than a LogRecordFactory: factories are global and the last one
    installed wins, which makes them hostile to a library that may be imported twice.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.session_id = session_id_var.get()
        record.request_id = request_id_var.get()
        return True


@asynccontextmanager
async def bind(
    *, session: str | None = None, request: str | None = None
) -> AsyncGenerator[None]:
    """Scope the ids for one turn. Wrap the turn, not individual log calls.

        async with bind(session=ctx.session_id, request=ctx.request_id):
            ...

    Tokens are reset on exit so a nested bind restores the outer turn's ids rather
    than clearing them -- and __aexit__ still runs under cancellation, so a cancelled
    turn cannot leak its ids into whatever the task does next.

    ContextVars set here reach the caller: awaiting the generator does not create a
    Task, so no context copy happens at the boundary. Tasks spawned INSIDE the block
    copy the bound values, which is what makes parallel tool calls log the right turn.
    """
    tokens = []
    if session is not None:
        tokens.append((session_id_var, session_id_var.set(session)))
    if request is not None:
        tokens.append((request_id_var, request_id_var.set(request)))
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


# ==============================================================================
# 3 · Formatters
# ==============================================================================

# Attribute names the stdlib puts on every record. Anything else came from
# logger.info("...", extra={...}) and is worth keeping.
_RESERVED: Final = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"message", "asctime", "taskName", "session_id", "request_id"}


class JsonFormatter(logging.Formatter):
    """One JSON object per line -- what the file gets.

    Machine-readable because the file exists to be grepped and shipped, not read.
    Human-facing output goes to the stderr handler in the human format below.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "at": f"{record.filename}:{record.lineno}",
            "session_id": getattr(record, "session_id", "-"),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # extra={...} lands here -- tool names, durations, decisions.
        extras = {k: v for k, v in record.__dict__.items() if k not in _RESERVED}
        if extras:
            payload["extra"] = extras
        return _dumps(payload)


CONSOLE_FORMAT: Final = (
    "%(asctime)s | %(levelname)-8s | %(session_id)s/%(request_id)s | "
    "%(filename)s:%(lineno)d | %(message)s"
)


# ==============================================================================
# 4 · Wiring
# ==============================================================================

ROOT_NAME: Final = "sera.agent"

_listener: logging.handlers.QueueListener | None = None
_configured = False


def configure(
    *,
    level: int | str = logging.INFO,
    log_dir: Path | str = DEFAULT_LOG_DIR,
    console_level: int | str | None = None,
    json_console: bool = False,
) -> logging.Logger:
    """Install the handlers. Idempotent -- safe under uvicorn reload and re-import.

    Returns the agent root logger. Call once from the entry point, before anything
    else runs; every module below just calls get_logger().
    """
    global _listener, _configured

    logger = logging.getLogger(ROOT_NAME)
    if _configured:
        return logger

    logger.setLevel(level)
    # Do not hand records to the root logger: uvicorn installs its own stdout
    # handler there, and inheriting it would put agent logs on the protocol stream.
    logger.propagate = False

    directory = Path(log_dir)
    directory.mkdir(parents=True, exist_ok=True)

    file_handler = logging.handlers.RotatingFileHandler(
        filename=directory / LOG_FILENAME,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
        delay=True,  # do not create the file until something is actually logged
    )
    file_handler.setFormatter(JsonFormatter())
    file_handler.setLevel(level)

    # sys.stderr, explicitly. StreamHandler() defaults to stderr already, but naming
    # it is the point: this is the invariant the protocol depends on.
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(
        JsonFormatter()
        if json_console
        else logging.Formatter(CONSOLE_FORMAT, datefmt="%H:%M:%S")
    )
    console_handler.setLevel(console_level if console_level is not None else level)

    # SimpleQueue: unbounded and lock-free on the put side, which is what the
    # cookbook recommends for a QueueHandler. An unbounded queue cannot drop a
    # record under load -- it trades memory for never losing the line that explains
    # the outage.
    record_queue: queue.SimpleQueue[logging.LogRecord] = queue.SimpleQueue()
    queue_handler = logging.handlers.QueueHandler(record_queue)
    queue_handler.addFilter(ContextFilter())

    logger.handlers.clear()
    logger.addHandler(queue_handler)

    _listener = logging.handlers.QueueListener(
        record_queue,
        file_handler,
        console_handler,
        respect_handler_level=True,
    )
    _listener.start()
    atexit.register(shutdown)

    _configured = True
    return logger


def shutdown() -> None:
    """Drain the queue and close the files. Registered with atexit by configure().

    Without this the process can exit with records still queued -- and the ones you
    lose are the ones written during the crash you are trying to diagnose.
    """
    global _listener, _configured
    if _listener is not None:
        _listener.stop()
        for handler in _listener.handlers:
            handler.close()
        _listener = None
    _configured = False


def get_logger(name: str | None = None) -> logging.Logger:
    """The only accessor modules should use.

    get_logger(__name__) from app.agent.tools.read gives sera.agent.tools.read, so a
    level can be raised or lowered for one subtree without touching the others.
    """
    if not name:
        return logging.getLogger(ROOT_NAME)
    suffix = name.removeprefix("app.agent.").removeprefix("app.")
    return logging.getLogger(f"{ROOT_NAME}.{suffix}")
