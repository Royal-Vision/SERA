import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from qdrant_client import models
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
    
    @staticmethod
    def _sha256_file(path: Path) -> str:
        """Return the SHA-256 hex digest of a file, read in 64KB chunks."""
        hasher = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(65_536):
                hasher.update(chunk)
        return hasher.hexdigest()

    def build_eval_dataset(self) -> pd.DataFrame:
        """Pure: source JSON -> eval DataFrame. No file writes.

        Returns columns: sample_id, question, ground_truth_answer, gold_doc_id.
        Deterministic given (SOURCE_PATH, EVAL_SIZE, RANDOM_SEED).
        """
        df = pd.read_json(self.SOURCE_PATH)

        if len(df) != self.EXPECTED_TOTAL_ROWS:
            raise ValueError(
                f"Source row count mismatch: expected {self.EXPECTED_TOTAL_ROWS}, "
                f"got {len(df)}. Refusing to build eval set from possibly-corrupted source."
            )

        shuffled = df.sample(frac=1, random_state=self.RANDOM_SEED).reset_index(drop=True)
        eval_rows = shuffled.iloc[: self.EVAL_SIZE].copy()

        out = pd.DataFrame({
            "sample_id": eval_rows["Question"].map(self.compute_sample_ids),
            "question": eval_rows["Question"],
            "ground_truth_answer": eval_rows["Response"],
        })
        out["gold_doc_id"] = out["sample_id"]
        out = out.sort_values("sample_id").reset_index(drop=True)

        logger.info("Built eval dataset: %d rows", len(out))
        return out

    def freeze_eval_dataset(self, df: pd.DataFrame) -> Path:
        """Write parquet + sidecar metadata. Returns the parquet path.

        Refuses to overwrite an existing frozen file — bump DATASET_VERSION
        to create v2 instead.
        """
        self.OUT_DIR.mkdir(parents=True, exist_ok=True)

        parquet_path = self.OUT_DIR / f"eval_gold_{self.DATASET_VERSION}.parquet"
        sidecar_path = self.OUT_DIR / f"eval_gold_{self.DATASET_VERSION}.meta.json"

        if parquet_path.exists():
            raise FileExistsError(
                f"{parquet_path} already exists. Refusing to overwrite a frozen "
                f"eval set — bump DATASET_VERSION to create a new one."
            )

        df.to_parquet(parquet_path, index=False, compression="snappy")

        metadata = {
            "version": self.DATASET_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "n_samples": len(df),
            "columns": list(df.columns),
            "source_file": self.SOURCE_PATH.name,
            "source_sha256": self._sha256_file(self.SOURCE_PATH),
            "dataset_sha256": self._sha256_file(parquet_path),
            "split_seed": self.RANDOM_SEED,
            "eval_size_constant": self.EVAL_SIZE,
        }
        sidecar_path.write_text(json.dumps(metadata, indent=2))

        logger.info(
            "Froze eval dataset: %s (sha256=%s)",
            parquet_path.name, metadata["dataset_sha256"][:12],
        )
        return parquet_path

    def load_eval_dataset(self, parquet_path: Path) -> tuple[pd.DataFrame, dict]:
        """Load + verify a frozen eval set. Raises if the parquet's hash drifted."""
        sidecar_path = parquet_path.with_suffix(".meta.json")

        if not parquet_path.exists():
            raise FileNotFoundError(f"Eval parquet not found: {parquet_path}")
        if not sidecar_path.exists():
            raise FileNotFoundError(f"Sidecar metadata not found: {sidecar_path}")

        metadata = json.loads(sidecar_path.read_text())
        actual_sha = self._sha256_file(parquet_path)

        if actual_sha != metadata["dataset_sha256"]:
            raise ValueError(
                f"Eval dataset hash mismatch!\n"
                f"  expected: {metadata['dataset_sha256']}\n"
                f"  actual:   {actual_sha}\n"
                f"The parquet was modified after freezing. Do not trust this run."
            )

        df = pd.read_parquet(parquet_path)
        logger.info(
            "Loaded eval dataset: %s (n=%d, sha256=%s)",
            parquet_path.name, len(df), actual_sha[:12],
        )
        return df, metadata

if __name__ == "__main__":
    dv = DatasetVersion()

    # 1. Index the full corpus into Qdrant with stable uuid5 ids (one-time)
    dv.evaluation_collection(batch_size=64)

    # 2. Build & freeze the 300-row eval set (one-time)
    parquet_path = dv.OUT_DIR / f"eval_gold_{dv.DATASET_VERSION}.parquet"
    if not parquet_path.exists():
        eval_df = dv.build_eval_dataset()
        parquet_path = dv.freeze_eval_dataset(eval_df)

    # 3. Round-trip verify
    loaded_df, meta = dv.load_eval_dataset(parquet_path)
    print(f"\n✅ Eval set frozen")
    print(f"   path           : {parquet_path}")
    print(f"   n_samples      : {meta['n_samples']}")
    print(f"   dataset_sha256 : {meta['dataset_sha256']}")
    print(f"   first sample   : {loaded_df['sample_id'].iloc[1]}")

    # 4. Smoke test — retrieve the first eval row's gold doc from Qdrant by id
    first_question = loaded_df["question"].iloc[1]
    record = dv.retrieve_point_id(question=first_question)
    logger.info(f"✅ gold doc retrieved by id: {record}")



