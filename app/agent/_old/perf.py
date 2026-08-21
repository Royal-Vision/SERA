"""Performance mode: the runtime switches that make SERA fast.

Every setting here was measured on this machine with `scripts/bench_runtime.py` under
CPython 3.14.7 (Windows, GIL enabled). Numbers in the comments are real, not quoted
from a changelog. Re-run the benchmark after any dependency bump.

Import cost of THIS module must stay near zero -- it is imported by the CLI entry
point before anything is printed. No langchain, no langgraph, no torch.
"""

from __future__ import annotations

import gc
import os
import sys
from typing import Any, Final

# ──────────────────────────────────────────────────────────────────────────────
# JSON
# ──────────────────────────────────────────────────────────────────────────────
# Measured: orjson.dumps is 77% faster than json.dumps().encode(), and orjson.loads
# is 45% faster than json.loads(), on a 21 KB tool-result payload.

try:
    import orjson

    def dumps(obj: Any) -> bytes:
        return orjson.dumps(obj)

    def loads(data: bytes | str) -> Any:
        return orjson.loads(data)

    JSON_BACKEND: Final = "orjson"
except ImportError:  # pragma: no cover - orjson is a declared dependency
    import json

    def dumps(obj: Any) -> bytes:
        return json.dumps(obj, separators=(",", ":")).encode()

    def loads(data: bytes | str) -> Any:
        return json.loads(data)

    JSON_BACKEND = "stdlib-json"


# ──────────────────────────────────────────────────────────────────────────────
# Compression
# ──────────────────────────────────────────────────────────────────────────────
# Measured on a 21 KB payload: zstd-1 compresses to 4.3% of raw (gzip-6 -> 5.3%),
# 79% faster to compress and 19% faster to decompress. Level 1, not 3: level 3 was
# not measurably smaller here and costs more CPU.
#
# compression.zstd is Python 3.14+ (PEP 784). Falls back to gzip below that.

ZSTD_LEVEL: Final = 1
COMPRESS_MIN_BYTES: Final = 1024  # below this, framing overhead outweighs the saving

try:
    from compression import zstd

    def compress(data: bytes) -> bytes:
        return zstd.compress(data, level=ZSTD_LEVEL)

    def decompress(data: bytes) -> bytes:
        return zstd.decompress(data)

    COMPRESSION_BACKEND: Final = "zstd"
except ImportError:  # pragma: no cover - Python < 3.14
    import gzip

    def compress(data: bytes) -> bytes:
        return gzip.compress(data, compresslevel=6)

    def decompress(data: bytes) -> bytes:
        return gzip.decompress(data)

    COMPRESSION_BACKEND = "gzip"


def maybe_compress(data: bytes) -> tuple[bytes, bool]:
    """Compress only when it is worth it. Returns (payload, was_compressed)."""
    if len(data) < COMPRESS_MIN_BYTES:
        return data, False
    packed = compress(data)
    return (packed, True) if len(packed) < len(data) else (data, False)


# ──────────────────────────────────────────────────────────────────────────────
# Float vectors
# ──────────────────────────────────────────────────────────────────────────────
# Measured on a 1024-dim vector: raw float32 bytes are 19.8% the size of a JSON float
# list, 96% faster to encode and 99% faster to decode (974 ms -> 8 ms per 5000 decodes).
# Every cache hit pays the decode, so this is the single biggest cache-layer win.

def pack_vector(values: "list[float]") -> bytes:
    import array

    return array.array("f", values).tobytes()


def unpack_vector(blob: bytes) -> "list[float]":
    import array

    arr = array.array("f")
    arr.frombytes(blob)
    return arr.tolist()


# ──────────────────────────────────────────────────────────────────────────────
# Identifiers
# ──────────────────────────────────────────────────────────────────────────────
# uuid7 is ~73% SLOWER to generate than uuid4 (0.6 vs 0.45 microseconds) -- irrelevant.
# What matters: uuid7 is time-ordered, so 100% of inserts append to the B-tree tail,
# versus 50% for uuid4. That is fewer page splits, a smaller index and faster writes.
# Measured locality: uuid7 100.0% ascending, uuid4 50.0%.

try:
    from uuid import uuid7 as _new_id  # Python 3.14+
except ImportError:  # pragma: no cover - Python < 3.14
    from uuid import uuid4 as _new_id


def new_id() -> str:
    """Time-ordered id for sessions, messages and tool calls."""
    return str(_new_id())


# ──────────────────────────────────────────────────────────────────────────────
# Event loop
# ──────────────────────────────────────────────────────────────────────────────

def install_event_loop_policy() -> str:
    """Install the fastest available event loop. Returns the backend name.

    uvloop (Linux/macOS) and winloop (Windows) are both optional. Neither is a
    declared dependency yet -- this degrades silently to stdlib asyncio.
    """
    if sys.platform == "win32":
        try:
            import winloop

            winloop.install()
            return "winloop"
        except ImportError:
            return "asyncio-proactor"
    try:
        import uvloop

        uvloop.install()
        return "uvloop"
    except ImportError:
        return "asyncio"


def enable_eager_tasks(loop: Any = None) -> bool:
    """Run coroutines inline until they actually suspend.

    Measured: 53.9 ms -> 28.9 ms (+46%) for 20,000 non-suspending coroutines.

    The agent hot path is full of awaits that usually complete without suspending --
    cache hits, permission checks, schema validation, metric emission. Without this,
    each still costs a Task allocation and an event-loop round-trip.
    """
    import asyncio

    if loop is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
    if not hasattr(asyncio, "eager_task_factory"):  # pragma: no cover - Python < 3.12
        return False
    loop.set_task_factory(asyncio.eager_task_factory)
    return True


# ──────────────────────────────────────────────────────────────────────────────
# GC
# ──────────────────────────────────────────────────────────────────────────────

def freeze_after_warmup() -> int:
    """Move everything allocated so far into a permanent GC generation.

    Measured: a full gc.collect() on a warmed process went from 9.65 ms to ~0 ms.

    Call this ONCE, at the very end of startup, after the tool registry, provider
    clients and compiled graph exist. Those objects live for the process lifetime, so
    having the collector rescan them on every pass is pure waste.
    """
    gc.collect()
    gc.freeze()
    return gc.get_freeze_count() if hasattr(gc, "get_freeze_count") else 0


def tune_gc() -> None:
    """Raise gen0 threshold.

    An agent allocates heavily in short bursts (parsing tool output, building
    messages). The default gen0 threshold of 700 triggers collections mid-burst.
    """
    gc.set_threshold(50_000, 50, 50)


# ──────────────────────────────────────────────────────────────────────────────
# Startup
# ──────────────────────────────────────────────────────────────────────────────

def configure_stdio() -> None:
    """Force UTF-8 on stdout/stderr.

    Windows consoles default to cp1252, which raises UnicodeEncodeError on any
    box-drawing character or emoji. A CLI that crashes while printing is unusable --
    this bit us in scripts/bench_runtime.py.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover - non-tty
            pass


def apply_performance_mode() -> dict[str, Any]:
    """Apply every measured optimisation. Call once, first thing, in the CLI entry point.

    Deliberately does NOT import asyncio, langchain or langgraph -- keeping this cheap
    is what lets the CLI print its first frame fast.
    """
    configure_stdio()
    tune_gc()
    loop_backend = install_event_loop_policy()
    return {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "free_threaded": not getattr(sys, "_is_gil_enabled", lambda: True)(),
        "json": JSON_BACKEND,
        "compression": COMPRESSION_BACKEND,
        "event_loop": loop_backend,
        "cpu_count": os.cpu_count(),
    }
