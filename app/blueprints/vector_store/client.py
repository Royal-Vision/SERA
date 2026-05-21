import uuid

from qdrant_client import QdrantClient, models
import pandas as pd
from app.configs.config import settings

client: QdrantClient = QdrantClient(
    url="http://localhost:6338",
    api_key=settings.QDRANT_API_KEY
)


client.delete_collection(collection_name="medical_o1_sft")
# create collection for retrieval
client.create_collection(
    collection_name="medical_o1_sft",
    vectors_config=models.VectorParams(
        size=1024, distance=models.Distance.COSINE, datatype=models.Datatype.UINT8,
        ),
)

def load_and_insert(
    file_path: str = r"C:\Users\ghoniem\Downloads\medical_o1_sft.json",
    batch_size: int = 32,
):
    df = pd.read_json(file_path)
    df = df[["Question", "Response"]]         # keep only what you need
 
    print(f"Loaded {len(df)} rows. Inserting in batches of {batch_size}...")
 
    for start in range(0, len(df), batch_size):
        batch = df.iloc[start : start + batch_size]
 
        # embed the Response — this is your knowledge, what gets retrieved
        embeddings = embedder.encode(
            batch["Response"].tolist(),
            normalize_embeddings=True,         # required for cosine similarity
        )
 
        points = [
            models.PointStruct(
                id=str(uuid.uuid4()),          # unique id per row
                vector=embeddings[i].tolist(),
                payload={
                    "question": row["Question"],
                    "response": row["Response"],
                },
            )
            for i, (_, row) in enumerate(batch.iterrows())
        ]
 
        client.upsert(collection_name=COLLECTION_NAME, points=points)
        print(f"  Inserted rows {start} → {start + len(batch)}")
 
    print("✅ Done.")

