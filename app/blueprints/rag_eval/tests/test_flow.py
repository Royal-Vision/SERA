"""End-to-end smoke test of the full eval flow.

WHAT IT TESTS — the entire pipeline runs cleanly on a tiny sample:
  load eval set → open MLflow run → evaluate 2 rows → scorers run → close run.

WHY IT EXISTS — unit tests don't catch integration issues (wrong scorer
config, missing API key, MLflow auth, S3 artifact storage, etc.). This test
is the single proof that the full pipeline composes end-to-end.

Marked @integration because it hits 3 external services:
  - MLflow at mlflow.ghoniem.online (run + artifact upload)
  - Qdrant at localhost:6338 (retrieval)
  - OpenAI (judge LLM, ~$0.005 per run)

Run:    pytest app/blueprints/rag_eval/tests/test_flow.py -m integration -v
"""

import pytest
import mlflow

from app.blueprints.rag_eval.mlflow_eval import RagEvalMLflow


@pytest.mark.integration
def test_eval_run_lifecycle(qdrant_reachable, mlflow_reachable):
    """Open eval_run context, log a dummy metric, close cleanly.

    WHAT IT TESTS — just the MLflow run lifecycle from RagEvalMLflow.eval_run:
      1. Find-or-create the commit parent run
      2. Open a child run under it
      3. Allow logging inside the with-block
      4. Close cleanly on exit

    WHY IT EXISTS — separates the MLflow plumbing (which needs only MLflow +
    Qdrant) from scoring (which also needs OpenAI). Lets you verify the run
    nesting works without paying for any LLM judge calls.

    Cost: $0. Required services: MLflow + Qdrant only (Qdrant because
    RagEvalMLflow.__init__ instantiates RagPipeline which connects to Qdrant).
    """
    ml = RagEvalMLflow()

    with ml.eval_run("pytest-eval-run-lifecycle") as e:
        # `e is ml` because eval_run yields self
        assert e is ml, "eval_run should yield the RagEvalMLflow instance"

        # Active run exists and we can log to it
        active = mlflow.active_run()
        assert active is not None, "no active MLflow run inside eval_run context"
        assert active.info.run_name == "pytest-eval-run-lifecycle"

        # Tags from base_tags are present
        tags = active.data.tags
        assert tags.get("run_type") == "eval"
        assert tags.get("pipeline") == "rag"
        assert "git_sha" in tags
        assert "git_branch" in tags

        # Caller can log inside the context
        mlflow.log_param("smoke_param", "lifecycle-test")
        mlflow.log_metric("smoke_metric", 0.99)

    # After the with-block, no run should be active
    assert mlflow.active_run() is None, (
        "eval_run did not clean up the active run on exit"
    )


@pytest.mark.integration
def test_run_evaluation_smoke(qdrant_reachable, mlflow_reachable, openai_key_set):
    """Open an MLflow run, evaluate 2 rows, close cleanly.

    Prechecks (via fixtures) verify all 3 external services are reachable
    BEFORE we burn time loading models or calling the LLM. Failure messages
    on prechecks tell you exactly what's wrong and how to fix it.
    """
    ml = RagEvalMLflow()

    try:
        with ml.eval_run("pytest-smoke-2rows"):
            results = ml.run_evaluation(sample_size=2)
    except ModuleNotFoundError as e:
        if "boto3" in str(e):
            pytest.fail(
                f"\n\n"
                f"❌ MLflow needs boto3 to upload artifacts to S3 storage.\n\n"
                f"To fix:\n"
                f"  uv add boto3\n\n"
                f"Why: your MLflow server (mlflow.ghoniem.online) is configured\n"
                f"with S3-backed artifact storage. boto3 is the S3 client.\n",
                pytrace=False,
            )
        raise
    except Exception as e:
        pytest.fail(
            f"\n\n"
            f"❌ Full eval flow crashed: {type(e).__name__}: {e}\n\n"
            f"Diagnostic checklist:\n"
            f"  1. Did Qdrant indexing run? (python -m app.blueprints.rag_eval.dataset)\n"
            f"  2. Is eval_gold_v1.parquet present in app/blueprints/data/?\n"
            f"  3. Is OPENAI_API_KEY valid and has credit?\n"
            f"  4. Check the MLflow UI for a partial run — may show where it failed\n",
            pytrace=True,
        )

    assert results is not None, (
        "mlflow.genai.evaluate returned None — the eval ran but produced no result. "
        "Check the MLflow UI for the run's metrics and traces."
    )
