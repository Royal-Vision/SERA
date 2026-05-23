import uuid 
import hashlib
from datetime import datetime
from pathlib import Path

import pandas as pd
from qdrant_client import QdrantClient, models
from tqdm import tqdm

from app.configs.logger import get_logger
from app.blueprints.vector_store.rag_pipeline import RagPipeline
logger = get_logger()


class DatasetVersion:
    SOURCE_PATH = Path(r"D:\etl\SERA-AI\app\blueprints\data\medical_o1_sft.json")

    # Frozen eval artifacts go here.
    OUT_DIR = Path(r"D:\etl\SERA-AI\app\blueprints\data")

    # Version your eval set. v2 means a NEW file, never overwrite v1.
    DATASET_VERSION = "v1"

    # Split contract — DO NOT change these constants after first freeze.
    # Changing them changes the rows in eval_gold → invalidates all prior MLflow runs.
    EVAL_SIZE = 300
    RANDOM_SEED = 42
    EXPECTED_TOTAL_ROWS = 19_704

    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self.rag_pipeline = RagPipeline()
        self._initialized = True

    def compute_sample_ids(self, question: str) -> str:
        """Deterministic UUIDv5 from question text.
        Same question text always produces the same id. This is what makes
        Hit@K exact-match comparisons possible without storing extra mappings.
        """
        return str(uuid.uuid5(namespace=uuid.NAMESPACE_DNS, name=question))

    def evaluation_collection(
        self,
        batch_size: int,
        collection_name: str = "evaluation_medical_o1_sft",
    ):
        client = self.rag_pipeline.client
        embedding = self.rag_pipeline.embedding

        if client.collection_exists(collection_name=collection_name):
            logger.info(f"Collection '{collection_name}' exists — skipping insert.")
            return

        client.create_collection(
            collection_name=collection_name,
            vectors_config=models.VectorParams(
                size=1024,
                distance=models.Distance.COSINE,
            ),
        )
        logger.info(f"Collection '{collection_name}' created.")

        df = pd.read_json(self.SOURCE_PATH)
        if len(df) != self.EXPECTED_TOTAL_ROWS:
            raise ValueError(
                f"Source row count mismatch: expected {self.EXPECTED_TOTAL_ROWS}, "
                f"got {len(df)}. Refusing to index possibly-corrupted source."
            )

        total_batches = (len(df) + batch_size - 1) // batch_size
        inserted, failed = 0, 0

        for start in tqdm(
            range(0, len(df), batch_size),
            total=total_batches,
            desc="Inserting",
            unit="batch",
        ):
            batch = df.iloc[start: start + batch_size]
            try:
                embeddings = embedding.encode(
                    batch["Response"].tolist(),
                    batch_size=batch_size,
                    max_length=512,
                )["dense_vecs"]

                points = [
                    models.PointStruct(
                        id=self.compute_sample_ids(question=row["Question"]),
                        vector=embeddings[i].tolist(),
                        payload={
                            "question": row["Question"],
                            "response": row["Response"],
                        },
                    )
                    for i, (_, row) in enumerate(batch.iterrows())
                ]

                client.upsert(collection_name=collection_name, points=points)
                inserted += len(batch)
            except (RuntimeError, ValueError) as e:
                failed += len(batch)
                logger.error(f"Batch {start} failed: {e}")

        logger.info(f"✅ Inserted: {inserted} | failed: {failed}")

    # def retrieve_point_id(self, question: str) -> str:
    #     client = self.rag_pipeline.client
    #     collection_name: str = "evaluation_medical_o1_sft"
    #     vector = self.rag_pipeline.embedding.encode(
    #         [question],
    #         max_length=512,
    #     )["dense_vecs"][0]

    #     results = client.query_points(
    #         collection_name=collection_name,
    #         query=vector,
    #         limit=1
    #     )

    #     return results


    def retrieve_point_id(self, question: str) -> str:
        gold_doc_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, question))

        results = self.rag_pipeline.client.retrieve(
            collection_name="evaluation_medical_o1_sft",
            ids=[gold_doc_id]
        )

        return results

if __name__ == "__main__":
    data_versions = DatasetVersion()
    
    """ --------- test the retrieval -------------------- """
    df = pd.read_json(data_versions.SOURCE_PATH)
    question_one = df["Question"][0]

    # convert to uuid 5
    question_uuid = data_versions.compute_sample_ids(question_one)
    logger.info(f"✅ question_uuid: {question_uuid}")

    # uuid is retrieved
    record = data_versions.retrieve_point_id(question=question_one)
    logger.info(f"✅ record: {record}")



