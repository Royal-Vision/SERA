
import asyncio
import contextlib
import subprocess

import mlflow

from app.blueprints.old.utilities.mlflow_tracker import tracker  # noqa: F401
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
        
        self._git_sha = "unknown"
        self._git_branch = "unknown"

        self._git_sha , self._git_branch = await asyncio.gather(
            self._get_git_sha(),
            self._get_git_branch(),
        )

        self._initialized = True

        logger.info(
            "RagEvalMLflow ready | sha=%s | branch=%s",
            self._git_sha, self._git_branch,
        )


    async def _get_git_sha(self):
        """Get the current git commit SHA."""
        try:
            result = await asyncio.subprocess.run(
                ["git", "rev-parse", "HEAD"],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                text=True,
                check=True,
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            logger.warning("Failed to get git SHA: %s", e)
            return "unknown"
    
    async def _get_git_branch(self):
        """Get the current git branch name."""
        try:
            result = await asyncio.subprocess.run(
                ["git", "branch", "--show-current"],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                text=True,
                check=True,
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            logger.warning("Failed to get git branch: %s", e)
            return "unknown"
        

    asy


if __name__ == "__main__":
    # Example usage
    rag_eval_mlflow = RagEvalMLflow()
    git_sha , git_branch = rag_eval_mlflow._git_sha, rag_eval_mlflow._git_branch
    print("Git SHA:", git_sha)
    print("Git Branch:", git_branch)

