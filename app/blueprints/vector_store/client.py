import os
import uuid
import pandas as pd
from tqdm import tqdm
from dotenv import load_dotenv
from qdrant_client import QdrantClient, models
from FlagEmbedding import BGEM3FlagModel

from app.configs.logger import get_logger

load_dotenv()
logger = get_logger()

COLLECTION_NAME = "medical_o1_sft"

embedder = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True)
client   = QdrantClient(
    url="http://localhost:6338",
    api_key=os.getenv("QDRANT_API_KEY"),
)

# ─────────────────────────────────────────────
# 1. Drop and recreate with correct size
# ─────────────────────────────────────────────

logger.info("Dropping existing collection...")
client.delete_collection(COLLECTION_NAME)

client.create_collection(
    collection_name=COLLECTION_NAME,
    vectors_config=models.VectorParams(
        size=1024,                        # ← bge-m3 dense dim, was wrong (100) before
        distance=models.Distance.COSINE,
    ),
)
logger.info(f"✅ Collection '{COLLECTION_NAME}' recreated with size=1024")


# ─────────────────────────────────────────────
# 2. Reinsert
# ─────────────────────────────────────────────

def load_and_insert(
    file_path: str = r"C:\Users\ghoniem\Downloads\medical_o1_sft.json",
    batch_size: int = 12,
):
    logger.info(f"Loading data from: {file_path}")
    df = pd.read_json(file_path)[["Question", "Response"]]

    total_rows    = len(df)
    total_batches = (total_rows + batch_size - 1) // batch_size
    logger.info(f"Loaded {total_rows} rows → {total_batches} batches of {batch_size}")

    inserted = 0
    failed   = 0

    for start in tqdm(range(0, total_rows, batch_size), total=total_batches, desc="Inserting", unit="batch"):
        batch = df.iloc[start : start + batch_size]

        try:
            embeddings = embedder.encode(
                batch["Response"].tolist(),
                batch_size=batch_size,
                max_length=512,
            )["dense_vecs"]

            # verify dim on first batch
            if start == 0:
                logger.info(f"First vector dim={len(embeddings[0])} | sum={embeddings[0].sum():.4f}")

            points = [
                models.PointStruct(
                    id=str(uuid.uuid4()),
                    vector=embeddings[i].tolist(),
                    payload={
                        "question": row["Question"],
                        "response": row["Response"],
                    },
                )
                for i, (_, row) in enumerate(batch.iterrows())
            ]

            client.upsert(collection_name=COLLECTION_NAME, points=points)
            inserted += len(batch)

        except Exception as e:
            failed += len(batch)
            logger.error(f"Batch {start}→{start + len(batch)} failed: {e}")

    logger.info(f"✅ Insert complete — inserted: {inserted} | failed: {failed} | total: {total_rows}")


load_and_insert()


# ─────────────────────────────────────────────
# 3. Verify
# ─────────────────────────────────────────────

info = client.get_collection(COLLECTION_NAME)
logger.info(f"✅ Collection verified — points_count: {info.points_count}")