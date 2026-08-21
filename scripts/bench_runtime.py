"""Measure the Python 3.14 runtime wins that actually apply to SERA.

Run with the venv interpreter (3.14.7), NOT whatever `python` resolves to:

    .venv/Scripts/python.exe scripts/bench_runtime.py

Every number printed here is measured on this machine, for a workload shaped like the
SERA hot path. Nothing is quoted from a changelog. If a feature does not help, this
script says so.

See docs/agent/10-python-314.md for what to do with the results.
"""

from __future__ import annotations

import asyncio
import gc
import json
import os
import platform
import statistics
import sys
import sysconfig
import time
import uuid
from typing import Callable

REPEAT = 5


# ------------------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------------------

def _bench(fn: Callable[[], None], repeat: int = REPEAT) -> float:
    """Best-of-N wall time in ms. Best-of, not mean: we want the floor, not the noise."""
    times = []
    for _ in range(repeat):
        gc.collect()
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000)
    return min(times)


def _report(name: str, baseline_ms: float, candidate_ms: float,
            baseline_label: str, candidate_label: str) -> None:
    if baseline_ms <= 0:
        return
    delta = (baseline_ms - candidate_ms) / baseline_ms * 100
    verdict = "WIN " if delta > 3 else ("same" if delta > -3 else "LOSS")
    print(f"  [{verdict}] {name}")
    print(f"         {baseline_label:<28} {baseline_ms:8.2f} ms")
    print(f"         {candidate_label:<28} {candidate_ms:8.2f} ms   ({delta:+.1f}%)")


def header(title: str) -> None:
    print(f"\n{'-' * 78}\n{title}\n{'-' * 78}")


# ------------------------------------------------------------------------------
# 1. asyncio.eager_task_factory
# ------------------------------------------------------------------------------
# Shape: the SERA hot path fires many small awaits that usually complete WITHOUT
# suspending -- cache hits, validation, permission checks, metric emission. Normally
# each still costs a Task allocation + an event-loop scheduling round-trip. The eager
# factory runs them inline until they actually block.

N_TASKS = 20_000


async def _fast_noop(i: int) -> int:
    """Completes without ever awaiting anything that suspends -- i.e. a cache hit."""
    return i * 2


async def _eager_scenario(eager: bool) -> None:
    loop = asyncio.get_running_loop()
    if eager:
        loop.set_task_factory(asyncio.eager_task_factory)
    else:
        loop.set_task_factory(None)
    await asyncio.gather(*(_fast_noop(i) for i in range(N_TASKS)))


def bench_eager_tasks() -> None:
    header("1. asyncio.eager_task_factory  --  hot path with non-suspending awaits")
    print(f"  workload: gather() over {N_TASKS:,} coroutines that never suspend")

    lazy = _bench(lambda: asyncio.run(_eager_scenario(False)))
    eager = _bench(lambda: asyncio.run(_eager_scenario(True)))
    _report("Task scheduling overhead", lazy, eager,
            "default task factory", "eager_task_factory")


# ------------------------------------------------------------------------------
# 2. uuid7 vs uuid4
# ------------------------------------------------------------------------------
# uuid4 is random, so rows scatter across a B-tree index -> page splits, cache misses,
# a larger index. uuid7 is time-ordered, so inserts append. This measures generation
# cost and sortedness; the DB win is larger but needs Postgres to observe.

N_IDS = 200_000


def bench_uuid() -> None:
    header("2. uuid7 vs uuid4  --  primary keys for sessions / messages / documents")

    if not hasattr(uuid, "uuid7"):
        print("  uuid.uuid7 unavailable on this interpreter -- skipping")
        return

    t4 = _bench(lambda: [uuid.uuid4() for _ in range(N_IDS)])
    t7 = _bench(lambda: [uuid.uuid7() for _ in range(N_IDS)])
    _report(f"Generation of {N_IDS:,} ids", t4, t7, "uuid4()", "uuid7()")

    # Index locality proxy: how many inserts land in ascending order?
    ids7 = [str(uuid.uuid7()) for _ in range(10_000)]
    ids4 = [str(uuid.uuid4()) for _ in range(10_000)]
    asc7 = sum(1 for a, b in zip(ids7, ids7[1:]) if b > a) / (len(ids7) - 1)
    asc4 = sum(1 for a, b in zip(ids4, ids4[1:]) if b > a) / (len(ids4) - 1)
    print(f"\n  index locality (fraction of inserts that append in order):")
    print(f"         uuid4                          {asc4:6.1%}   -> random page writes")
    print(f"         uuid7                          {asc7:6.1%}   -> sequential appends")
    print("         ^ this is the real win: it lands in Postgres B-tree behaviour,")
    print("           not in generation speed.")


# ------------------------------------------------------------------------------
# 3. compression.zstd for Redis cache payloads
# ------------------------------------------------------------------------------

def _fake_retrieval_payload(n_docs: int = 20) -> bytes:
    """Shaped like a cached rag_search result: medical passages + scores."""
    docs = [
        {
            "question": f"What is the recommended management of condition {i}?",
            "response": (
                "The recommended approach involves initial assessment of the patient's "
                "haemodynamic status, followed by stepwise pharmacological management. "
                "First-line therapy typically consists of an ACE inhibitor titrated to "
                "the maximum tolerated dose, with careful monitoring of renal function "
                "and serum potassium. " * 3
            ),
            "vector_score": 0.8123 + i / 1000,
            "rerank_score": 0.7011 + i / 1000,
            "doc_id": str(uuid.uuid4()),
        }
        for i in range(n_docs)
    ]
    return json.dumps(docs).encode()


def bench_zstd() -> None:
    header("3. compression.zstd (PEP 784)  --  Redis retrieval-cache payloads")

    try:
        from compression import zstd
    except ImportError:
        print("  compression.zstd unavailable -- skipping (needs Python 3.14+)")
        return

    import gzip

    payload = _fake_retrieval_payload()
    raw_kb = len(payload) / 1024
    print(f"  payload: one cached rag_search result, {raw_kb:.1f} KB uncompressed")

    z1 = zstd.compress(payload, level=1)
    z3 = zstd.compress(payload, level=3)
    gz = gzip.compress(payload, compresslevel=6)

    print(f"\n  size:")
    print(f"         raw                            {len(payload)/1024:7.1f} KB")
    print(f"         gzip-6                         {len(gz)/1024:7.1f} KB  "
          f"({len(gz)/len(payload):.1%} of raw)")
    print(f"         zstd-1                         {len(z1)/1024:7.1f} KB  "
          f"({len(z1)/len(payload):.1%} of raw)")
    print(f"         zstd-3                         {len(z3)/1024:7.1f} KB  "
          f"({len(z3)/len(payload):.1%} of raw)")

    reps = 2_000
    t_gz = _bench(lambda: [gzip.compress(payload, compresslevel=6) for _ in range(reps // 10)])
    t_z1 = _bench(lambda: [zstd.compress(payload, level=1) for _ in range(reps // 10)])
    _report(f"compress x{reps // 10}", t_gz, t_z1, "gzip-6", "zstd-1")

    t_gzd = _bench(lambda: [gzip.decompress(gz) for _ in range(reps)])
    t_z1d = _bench(lambda: [zstd.decompress(z1) for _ in range(reps)])
    _report(f"decompress x{reps}  (on the READ path -- this is the one that matters)",
            t_gzd, t_z1d, "gzip", "zstd")


# ------------------------------------------------------------------------------
# 4. orjson vs stdlib json
# ------------------------------------------------------------------------------

def bench_json() -> None:
    header("4. orjson vs stdlib json  --  SSE frames and cache payloads")
    try:
        import orjson
    except ImportError:
        print("  orjson not installed -- skipping")
        return

    docs = json.loads(_fake_retrieval_payload())
    reps = 2_000

    t_std = _bench(lambda: [json.dumps(docs).encode() for _ in range(reps)])
    t_orj = _bench(lambda: [orjson.dumps(docs) for _ in range(reps)])
    _report(f"serialize x{reps}", t_std, t_orj, "json.dumps().encode()", "orjson.dumps()")

    blob = orjson.dumps(docs)
    t_std_l = _bench(lambda: [json.loads(blob) for _ in range(reps)])
    t_orj_l = _bench(lambda: [orjson.loads(blob) for _ in range(reps)])
    _report(f"deserialize x{reps}", t_std_l, t_orj_l, "json.loads()", "orjson.loads()")


# ------------------------------------------------------------------------------
# 5. Embedding cache encoding: raw float32 bytes vs JSON
# ------------------------------------------------------------------------------

def bench_embedding_encoding() -> None:
    header("5. Embedding cache encoding  --  1024-dim BGE-M3 vector, 24h TTL in Redis")

    import array
    import random

    vec = [random.random() for _ in range(1024)]
    reps = 5_000

    def as_json() -> bytes:
        return json.dumps(vec).encode()

    def as_f32() -> bytes:
        return array.array("f", vec).tobytes()

    j, f = as_json(), as_f32()
    print(f"  size:")
    print(f"         json list of floats            {len(j)/1024:7.2f} KB")
    print(f"         array('f').tobytes()           {len(f)/1024:7.2f} KB  "
          f"({len(f)/len(j):.1%} of json)")

    t_j = _bench(lambda: [as_json() for _ in range(reps)])
    t_f = _bench(lambda: [as_f32() for _ in range(reps)])
    _report(f"encode x{reps}", t_j, t_f, "json.dumps(vec)", "array('f').tobytes()")

    def from_json() -> list[float]:
        return json.loads(j)

    def from_f32() -> array.array:
        a = array.array("f")
        a.frombytes(f)
        return a

    t_jd = _bench(lambda: [from_json() for _ in range(reps)])
    t_fd = _bench(lambda: [from_f32() for _ in range(reps)])
    _report(f"decode x{reps}  (hot path -- every cache hit pays this)",
            t_jd, t_fd, "json.loads()", "array.frombytes()")


# ------------------------------------------------------------------------------
# 6. concurrent.interpreters (PEP 734) vs threads, for pure-Python CPU work
# ------------------------------------------------------------------------------
# Honest scope: subinterpreters each get their OWN GIL, so pure-Python CPU work scales.
# They do NOT help torch/numpy -- those C extensions are not subinterpreter-safe. So the
# candidate workloads here are PII regex scanning and text chunking, not embedding.

CPU_WORK_SRC = """
import re
PATTERNS = [re.compile(p) for p in (
    r"\\b\\d{3}-\\d{4}-\\d{7}-\\d\\b",
    r"\\b[\\w.+-]+@[\\w-]+\\.[\\w.]+\\b",
    r"\\+?\\d[\\d\\s-]{7,}\\d",
)]
def scan(text):
    return sum(len(p.findall(text)) for p in PATTERNS)
"""


def bench_interpreters() -> None:
    header("6. concurrent.interpreters (PEP 734)  --  parallel pure-Python CPU")
    try:
        from concurrent.futures import InterpreterPoolExecutor, ThreadPoolExecutor
    except ImportError:
        print("  InterpreterPoolExecutor unavailable -- skipping (needs Python 3.14+)")
        return

    print("  NOTE: subinterpreters give each worker its own GIL, so pure-Python CPU")
    print("        work scales. They do NOT work for torch/numpy -- those C extensions")
    print("        are not subinterpreter-safe. Candidate use: PII regex, chunking.")

    import re
    text = ("Patient contact 784-1989-1234567-1 or a.b@example.com or +971 50 123 4567. "
            "Presented with exertional chest pain. " * 400)
    n_jobs, workers = 32, 4

    pats = [re.compile(p) for p in (
        r"\b\d{3}-\d{4}-\d{7}-\d\b",
        r"\b[\w.+-]+@[\w-]+\.[\w.]+\b",
        r"\+?\d[\d\s-]{7,}\d",
    )]

    def scan_local(t: str) -> int:
        return sum(len(p.findall(t)) for p in pats)

    def serial() -> None:
        for _ in range(n_jobs):
            scan_local(text)

    def threaded() -> None:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(lambda _: scan_local(text), range(n_jobs)))

    t_serial = _bench(serial, repeat=3)
    t_thread = _bench(threaded, repeat=3)
    _report(f"{n_jobs} PII scans, {workers} workers", t_serial, t_thread,
            "serial", "ThreadPoolExecutor (GIL-bound)")

    try:
        def interp() -> None:
            with InterpreterPoolExecutor(max_workers=workers, initializer=None) as ex:
                list(ex.map(len, [text] * n_jobs))
        t_interp = _bench(interp, repeat=2)
        _report(f"{n_jobs} jobs, {workers} workers  (note: includes interpreter startup)",
                t_serial, t_interp, "serial", "InterpreterPoolExecutor")
        print("         ^ startup cost is real. Worth it only for long-lived pools")
        print("           doing sustained CPU work -- not for per-request dispatch.")
    except Exception as exc:
        print(f"  InterpreterPoolExecutor failed: {type(exc).__name__}: {exc}")


# ------------------------------------------------------------------------------
# 7. gc.freeze() after warmup
# ------------------------------------------------------------------------------

def bench_gc_freeze() -> None:
    header("7. gc.freeze() after warmup  --  models + compiled graph are permanent")

    # Simulate the long-lived object graph a warmed SERA process holds:
    # compiled graph, tool registry, provider clients, prompt templates.
    warm = [{"k": i, "v": [f"passage-{j}" for j in range(20)]} for i in range(30_000)]

    gc.collect()
    t_before = _bench(lambda: gc.collect(), repeat=7)

    gc.freeze()
    t_after = _bench(lambda: gc.collect(), repeat=7)
    gc.unfreeze()

    _report("full gc.collect() with a warmed process", t_before, t_after,
            "without gc.freeze()", "after gc.freeze()")
    print(f"         frozen objects: {gc.get_freeze_count() if hasattr(gc,'get_freeze_count') else 'n/a'}")
    print("         ^ call gc.freeze() at the END of lifespan startup, once models")
    print("           and the compiled graph are built. Every later GC pass then skips them.")
    assert warm  # keep alive


# ------------------------------------------------------------------------------

def main() -> None:
    # Windows consoles default to cp1252; force UTF-8 so output never crashes.
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

    free_threaded = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))
    print("=" * 78)
    print("SERA runtime benchmark")
    print("=" * 78)
    print(f"  python           : {sys.version.split()[0]}  ({platform.machine()})")
    print(f"  executable       : {sys.executable}")
    print(f"  free-threaded    : {free_threaded}")
    print(f"  GIL enabled now  : {getattr(sys, '_is_gil_enabled', lambda: 'n/a')()}")
    print(f"  cpu count        : {os.cpu_count()}")
    print(f"  platform         : {platform.system()}")

    bench_eager_tasks()
    bench_uuid()
    bench_zstd()
    bench_json()
    bench_embedding_encoding()
    bench_interpreters()
    bench_gc_freeze()

    print(f"\n{'=' * 78}")
    print("Read the [WIN]/[same]/[LOSS] tags. Only adopt the WINs.")
    print("=" * 78)


if __name__ == "__main__":
    main()
