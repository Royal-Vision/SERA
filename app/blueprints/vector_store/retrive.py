from app.configs.logger import get_logger
from qdrant_client import QdrantClient
from FlagEmbedding import BGEM3FlagModel

logger = get_logger()


def retrieve(
    client: QdrantClient,
    collection_name: str,
    embedder: BGEM3FlagModel,
    query: str,
    top_k: int = 20,
) -> list[dict]:
    logger.info(f"Retrieving top-{top_k} for query: '{query}'")

    try:
        # 1. Embed query
        query_vector = embedder.encode(
            [query],
            batch_size=1,
            max_length=512,
        )["dense_vecs"][0].tolist()

        logger.info(f"Query embedded — dim: {len(query_vector)} | sum: {sum(query_vector):.4f}")

        # 2. Guard — zero vector means embedder failed
        if sum(abs(v) for v in query_vector) == 0:
            logger.error("❌ Query vector is all zeros — embedder failed")
            return []

        # 3. Search — no score_threshold, bge-m3 scores can be negative
        results = client.query_points(
            collection_name=collection_name,
            query=query_vector,
            limit=top_k,
            with_payload=True,
            # score_threshold removed — bge-m3 cosine scores can be negative
            # setting 0.0 blocks all negative-score results
        ).points

        if not results:
            logger.warning("⚠️ No results returned from Qdrant")
            return []

        hits = [
            {
                "score":    hit.score,
                "question": hit.payload["question"],
                "response": hit.payload["response"],
            }
            for hit in results
        ]

        logger.info(f"✅ Retrieved {len(hits)} | top={hits[0]['score']:.4f} | low={hits[-1]['score']:.4f}")
        return hits

    except Exception as e:
        logger.error(f"❌ Retrieval failed for query '{query}': {e}")
        return []