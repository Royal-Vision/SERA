
import contextlib
import subprocess

import mlflow

from app.blueprints.utilities.mlflow_tracker import tracker  # noqa: F401
from app.configs.logger import get_logger

logger = get_logger()


class RagEvalMLflow:
    """RAG evaluation MLflow wrapper. Singleton.

    Wraps mlflow.* for RAG-specific patterns: eval_run context manager,
    namespaced metric logging, dataset fingerprinting, artifact helpers.

    Composes with raw mlflow.* — any mlflow call inside an active run
    just works regardless of how the run was started.
    """

    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._git_sha = self._get_git_sha()
        self._git_branch = self._get_git_branch()
        self._initialized = True

        logger.info(
            "RagEvalMLflow ready | sha=%s | branch=%s",
            self._git_sha, self._git_branch,
        )
