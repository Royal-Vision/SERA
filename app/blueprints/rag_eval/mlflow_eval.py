
from contextlib import contextmanager
import asyncio
from typing import Dict, List
import mlflow
from uuid import uuid5

import torch
from app.blueprints.utilities.mlflow_tracker import tracker  # noqa: F401
from app.blueprints.vector_store.rag_pipeline import RetrievedDoc
from app.configs.logger import get_logger
from mlflow.tracking import MlflowClient
from app.configs.config import settings
from app.blueprints.vector_store.rag_pipeline import RagPipeline
torch.cuda.empty_cache()

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
        self._initialized = True
        self._rag_pipeline = RagPipeline()


    async def _get_git_sha(self) -> str:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "rev-parse",
            "HEAD",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise RuntimeError(
                f"Failed to get git SHA: {stderr.decode().strip()}"
            )

        return stdout.decode().strip()
    
    async def _get_git_branch(self) -> str:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "rev-parse",
            "--abbrev-ref",
            "HEAD",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise RuntimeError(
                f"Failed to get git branch: {stderr.decode().strip()}"
            )

        return stdout.decode().strip()
    
    async def _check_evaluation_run_exists(self, ):
        

        client = MlflowClient(tracking_uri="https://mlflow.ghoniem.online")

        experiment = client.get_experiment_by_name("sera-ai")

        runs = client.search_runs(
            experiment_ids=[experiment.experiment_id]
        )
        logger.info(f"✅ experiment id : {experiment.experiment_id}")
        return runs

    def _get_or_create_commit_parent_run(self) -> str:
        client = MlflowClient(tracking_uri=settings.MLFLOW_TRACKING_URI)

        experiment = client.get_experiment_by_name(settings.EXPERIMENT_NAME)
        if experiment is None:
            experiment_id = client.create_experiment(settings.EXPERIMENT_NAME)
        else:
            experiment_id = experiment.experiment_id

        parent_name = f"commit:{self._git_branch}:{self._git_sha[:8]}"

        filter_string = (
            f"tags.run_type = 'commit_parent' "
            f"and tags.git_sha = '{self._git_sha}' "
            f"and tags.git_branch = '{self._git_branch}'"
        )

        runs = client.search_runs(
            experiment_ids=[experiment_id],
            filter_string=filter_string,
            max_results=1,
            order_by=["attributes.start_time DESC"],
        )

        if runs:
            return runs[0].info.run_id

        parent_run = client.create_run(
            experiment_id=experiment_id,
            tags={
                "mlflow.runName": parent_name,
                "run_type": "commit_parent",
                "pipeline": "rag",
                "git_sha": self._git_sha,
                "git_branch": self._git_branch,
                "commit_short": self._git_sha[:8],
            },
        )

        logger.info(
            "Created parent MLflow run for commit %s branch %s: %s",
            self._git_sha,
            self._git_branch,
            parent_run.info.run_id,
        )

        return parent_run.info.run_id

    @contextmanager
    def eval_run(self, run_name: str, tags: dict | None = None):
        parent_run_id = self._get_or_create_commit_parent_run()

        base_tags = {
            "run_type": "eval",
            "pipeline": "rag",
            "git_sha": self._git_sha,
            "git_branch": self._git_branch,
            "parent_type": "commit",
        }

        if tags:
            base_tags.update(tags)

        with mlflow.start_run(
            run_name=run_name,
            parent_run_id=parent_run_id,
            tags=base_tags,
        ) as active:
            logger.info(
                "MLflow child eval run started: %s (%s) under parent %s",
                run_name,
                active.info.run_id,
                parent_run_id,
            )

            yield self

            logger.info("MLflow child eval run ended: %s", active.info.run_id)



async def _test():
    rag = RagEvalMLflow()

    git_sha, git_branch = await asyncio.gather(
        rag._get_git_sha(),
        rag._get_git_branch(),
    )

    print(f"Git SHA   : {git_sha}")
    print(f"Git Branch: {git_branch}")


if __name__ == "__main__":
    runs = asyncio.run(RagEvalMLflow()._check_evaluation_run_exists())
    print(f"runs: {runs}")

