
import subprocess
from contextlib import contextmanager

import mlflow
from mlflow.tracking import MlflowClient

from app.blueprints.utilities.mlflow_tracker import tracker  # noqa: F401
from app.blueprints.vector_store.rag_pipeline import RagPipeline
from app.blueprints.rag_eval.dataset import DatasetVersion
from app.configs.config import settings
from app.configs.logger import get_logger

from mlflow.genai.scorers import (
    RetrievalRelevance, RetrievalSufficiency, 
    Correctness, Completeness, Fluency, RelevanceToQuery
    )
from mlflow.genai import evaluate

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
        
        self._rag_pipeline = RagPipeline()
        self._dataset      = DatasetVersion()
        self._git_sha      = self._get_git_sha()
        self._git_branch   = self._get_git_branch()
        self._initialized  = True

        logger.info(
            "RagEvalMLflow ready | sha=%s | branch=%s",
            self._git_sha, self._git_branch,
        )

    @staticmethod
    def _get_git_sha() -> str:
        try:
            return subprocess.check_output(
                ["git", "rev-parse", "HEAD"], text=True
            ).strip()
        except Exception:
            return "unknown"

    @staticmethod
    def _get_git_branch() -> str:
        try:
            return subprocess.check_output(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"], text=True
            ).strip()
        except Exception:
            return "unknown"

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
        """Pure context manager: open MLflow run under commit parent, yield, close."""
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
                run_name, active.info.run_id, parent_run_id,
            )
            yield self
            logger.info("MLflow child eval run ended: %s", active.info.run_id)

    def run_evaluation(self, sample_size: int | None = None):
        """Run the evaluation. Must be called inside an active eval_run() context.

        Parameters
        ----------
        sample_size : int | None
            If set, evaluate only the first N rows. Use small (e.g. 5) for
            smoke tests; None runs the full eval set.
        """
        parquet_path = (
            self._dataset.OUT_DIR
            / f"eval_gold_{self._dataset.DATASET_VERSION}.parquet"
        )
        eval_df, metadata = self._dataset.load_eval_dataset(parquet_path=parquet_path)

        if sample_size:
            eval_df = eval_df.head(sample_size)

        # ── params & tags inside the active run ──
        mlflow.log_param("top_k", settings.TOP_K)
        mlflow.log_param("top_n", settings.TOP_N)
        mlflow.log_param("embedder", "BAAI/bge-m3")
        mlflow.log_param("reranker", "jinaai/jina-reranker-v3")
        mlflow.log_param("judge_model", "openai:/gpt-4o-mini-2024-07-18")
        mlflow.log_param("eval_size_actual", len(eval_df))

        mlflow.set_tag("eval_dataset_sha256", metadata["dataset_sha256"])
        mlflow.set_tag("eval_dataset_version", metadata["version"])
        mlflow.set_tag("eval_dataset_full_size", str(metadata["n_samples"]))

        mlflow.log_artifact(str(parquet_path), artifact_path="eval_dataset")

        # ── eval: project to only the columns predict_fn needs ──
        results = mlflow.genai.evaluate(
            data=eval_df[["question"]],
            predict_fn=self.predict_fn,
            scorers=[RetrievalRelevance(), RetrievalSufficiency()],
        )
        return results
    
    @mlflow.trace(span_type="CHAIN")
    def predict_fn(self, question: str) -> dict:
        """Called by mlflow.genai.evaluate per eval row.

        Tier 1: retrieval-only. Response is empty (no generator yet).
        The RETRIEVER + RERANKER spans come from the decorators on RagPipeline,
        which is what the RAG judges read to score retrieval quality.
        """

        re_ranked = self._rag_pipeline.retrieve_and_rerank(query=question)

        retrieved_context= [
                            {
                                "content": d.response,
                                "doc_uri": f"qdrant://{settings.COLLECTION_NAME}",
                            }
                            for d in re_ranked
                        ]

        return {
                "response": "",
                "retrieved_context": retrieved_context
            }
