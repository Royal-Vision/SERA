"""Shared fixtures + service health checks for rag_eval tests.

The health checks turn cryptic connection errors into clear, actionable
failure messages so you know exactly what to fix.
"""

import socket
from urllib.parse import urlparse

import pytest

from app.blueprints.rag_eval.dataset import DatasetVersion
from app.configs.config import settings


# ───────────────────────── pure dataset fixtures ─────────────────────────

@pytest.fixture
def dv() -> DatasetVersion:
    """The DatasetVersion singleton — safe to reuse across tests."""
    return DatasetVersion()


@pytest.fixture
def isolated_dv(tmp_path, monkeypatch) -> DatasetVersion:
    """DatasetVersion with OUT_DIR redirected to a temp dir.

    Use this for tests that write parquet/sidecar so they don't collide
    with the real eval_gold_v1.parquet on disk.
    """
    d = DatasetVersion()
    monkeypatch.setattr(d, "OUT_DIR", tmp_path)
    monkeypatch.setattr(d, "DATASET_VERSION", "test")
    return d


# ───────────────────────── service health checks ─────────────────────────

def _tcp_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    """Return True if a TCP socket can connect to host:port."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def qdrant_reachable() -> bool:
    """Skip integration tests cleanly when Qdrant isn't running.

    Qdrant URL comes from the QDRANT_URL env var or defaults to
    http://localhost:6338 in RagPipeline.__init__.
    """
    import os
    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6338")
    parsed = urlparse(qdrant_url)
    host, port = parsed.hostname, parsed.port or 6338

    if not _tcp_reachable(host, port):
        pytest.fail(
            f"\n\n"
            f"❌ Qdrant is not reachable at {qdrant_url}\n\n"
            f"To fix:\n"
            f"  1. Start Qdrant locally:\n"
            f"     docker run -p 6338:6333 qdrant/qdrant\n"
            f"  2. OR set QDRANT_URL env var to a reachable instance\n"
            f"  3. Then re-run the test\n",
            pytrace=False,
        )
    return True


@pytest.fixture(scope="session")
def mlflow_reachable() -> bool:
    """Skip integration tests cleanly when MLflow tracking server is down."""
    parsed = urlparse(settings.MLFLOW_TRACKING_URI)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    if not _tcp_reachable(host, port):
        pytest.fail(
            f"\n\n"
            f"❌ MLflow tracking server is not reachable at {settings.MLFLOW_TRACKING_URI}\n\n"
            f"To fix:\n"
            f"  1. Check the URL in app/configs/config.py (settings.MLFLOW_TRACKING_URI)\n"
            f"  2. Verify network/VPN to mlflow.ghoniem.online\n"
            f"  3. Then re-run the test\n",
            pytrace=False,
        )
    return True


@pytest.fixture(scope="session")
def openai_key_set() -> bool:
    """Skip judge-based tests cleanly when OPENAI_API_KEY is missing."""
    if not settings.OPENAI_API_KEY:
        pytest.fail(
            f"\n\n"
            f"❌ OPENAI_API_KEY is not set in settings.\n\n"
            f"The MLflow RAG judge calls OpenAI to score each sample.\n"
            f"Without a key, mlflow.genai.evaluate(scorers=[...]) will fail.\n\n"
            f"To fix:\n"
            f"  1. Add OPENAI_API_KEY=sk-... to your .env\n"
            f"  2. Then re-run the test\n",
            pytrace=False,
        )
    return True
