"""Shape verification for RagEvalMLflow.predict_fn.

WHAT IT TESTS — that the bridge function (called by mlflow.genai.evaluate per
eval row) returns the exact dict shape MLflow's RAG scorers require. If this
shape is wrong, scorers silently produce wrong results or fail at evaluation.

WHY THESE TESTS EXIST — the dict shape is a contract with MLflow. We can't
trust it works without exercising the real RagPipeline (BGE-M3 + Jina + Qdrant)
because mocking the embedder hides shape bugs in the integration glue.

Marked @integration because it loads embedder/reranker models and hits Qdrant.
Run:    pytest -m integration -v
Skip:   pytest -m "not integration"
"""

import pytest

from app.blueprints.rag_eval.mlflow_eval import RagEvalMLflow
from app.configs.config import settings


# `qdrant_reachable` is a session-scoped precheck that auto-fails with a clear
# message if Qdrant isn't running. We list it as a dep on the ml fixture so
# the precheck runs BEFORE we waste 30 seconds loading models.

@pytest.fixture(scope="module")
def ml(qdrant_reachable) -> RagEvalMLflow:
    """Module-scoped: load models + Qdrant client once, share across tests."""
    return RagEvalMLflow()


@pytest.mark.integration
class TestPredictFnShape:
    QUESTION = "What are the symptoms of diabetes?"

    def test_returns_dict_with_required_keys(self, ml):
        """WHY: MLflow scorers read `response` and `retrieved_context` by name.
        Missing keys → KeyError inside the scorer, eval crashes."""
        out = ml.predict_fn(self.QUESTION)
        assert isinstance(out, dict), (
            f"predict_fn must return dict, got {type(out).__name__}. "
            f"Check the `return` statement in mlflow_eval.predict_fn."
        )
        assert "response" in out, (
            f"predict_fn output missing 'response' key. Keys: {list(out)}. "
            f"MLflow generation scorers (Faithfulness, Correctness) need this."
        )
        assert "retrieved_context" in out, (
            f"predict_fn output missing 'retrieved_context' key. Keys: {list(out)}. "
            f"MLflow retrieval scorers (RetrievalRelevance) need this."
        )

    def test_response_empty_for_tier1(self, ml):
        """WHY: Tier 1 has no generator. Response MUST be empty string.
        If non-empty, you may be running an old version of predict_fn."""
        out = ml.predict_fn(self.QUESTION)
        assert out["response"] == "", (
            f"Tier 1 predict_fn should return empty response, got {out['response']!r}. "
            f"If you added a generator, mark this test xfail until Tier 2 lands."
        )

    def test_retrieved_context_is_non_empty_list(self, ml):
        """WHY: If retrieval returned 0 docs, the RAG scorers have nothing to
        score and the whole eval is meaningless. Empty list usually means:
          1. Qdrant collection is empty (run DatasetVersion.evaluation_collection)
          2. Qdrant collection name mismatch (check settings.COLLECTION_NAME)
          3. Embedding model produced a zero vector (rare, see retrieve())"""
        out = ml.predict_fn(self.QUESTION)
        ctx = out["retrieved_context"]
        assert isinstance(ctx, list), (
            f"retrieved_context must be a list, got {type(ctx).__name__}"
        )
        if len(ctx) == 0:
            pytest.fail(
                f"\n\n"
                f"❌ Retrieval returned 0 documents for query: {self.QUESTION!r}\n\n"
                f"Most likely causes:\n"
                f"  1. Qdrant collection '{settings.COLLECTION_NAME}' is empty\n"
                f"     → Run: python -m app.blueprints.rag_eval.dataset\n"
                f"  2. Collection name mismatch — check settings.COLLECTION_NAME\n"
                f"  3. Embedder produced a zero vector (check rag_pipeline.retrieve)\n"
                f"  4. The broad `except Exception` in RagPipeline.retrieve\n"
                f"     swallowed a real error — check stdout for 'Retrieval failed:'\n",
                pytrace=False,
            )

    def test_each_context_item_has_required_keys(self, ml):
        """WHY: MLflow's RetrievalRelevance scorer reads `content` from each
        context item. Missing `content` → scorer raises KeyError on every row."""
        out = ml.predict_fn(self.QUESTION)
        if not out["retrieved_context"]:
            pytest.skip("retrieved_context empty — covered by earlier test")

        for i, item in enumerate(out["retrieved_context"]):
            assert "content" in item, (
                f"context item {i} missing 'content' key. Item keys: {list(item)}"
            )
            assert "doc_uri" in item, (
                f"context item {i} missing 'doc_uri' key. Item keys: {list(item)}"
            )
            assert isinstance(item["content"], str) and len(item["content"]) > 0, (
                f"context item {i} 'content' must be non-empty string"
            )

    def test_context_count_matches_top_n(self, ml):
        """WHY: top_n controls how many docs reach the LLM in production.
        If predict_fn returns a different count, your eval doesn't measure
        production behavior — silent divergence."""
        out = ml.predict_fn(self.QUESTION)
        if not out["retrieved_context"]:
            pytest.skip("retrieved_context empty — covered by earlier test")

        assert len(out["retrieved_context"]) == settings.TOP_N, (
            f"Expected {settings.TOP_N} contexts (settings.TOP_N), "
            f"got {len(out['retrieved_context'])}. Check rerank top_n config."
        )
