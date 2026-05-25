from pathlib import Path
from typing import List

import pandas as pd
import os
import uuid
import torch
import asyncio
import mlflow
import pandas as pd
from tqdm import tqdm
from dataclasses import dataclass
from dotenv import load_dotenv
from transformers import AutoModel
from FlagEmbedding import BGEM3FlagModel
from qdrant_client import QdrantClient, models
from openai import AsyncOpenAI
from ragas.dataset_schema import SingleTurnSample
from ragas.metrics.collections import (
    ContextPrecision,
    ContextRecall,
    Faithfulness,
    # ResponseRelevancy,
)
from ragas.llms import llm_factory
from pydantic import BaseModel
from app.configs.config import settings
from app.configs.logger import get_logger
logger = get_logger()

DATA_PATH=r"D:\etl\SERA-AI\app\blueprints\data\medical_o1_sft.json"


class RetrievedDoc(BaseModel):
    question:     str
    response:     str
    vector_score: float
    rerank_score: float = 0.0


class RagPipeline:
    _instance    = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        logger.info("Loading models (runs once)...")
        self.embedding = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True)
        self.ranker = AutoModel.from_pretrained(
            "jinaai/jina-reranker-v3",
            dtype="auto",
            trust_remote_code=True,
        )
        self.ranker.eval()

        self.client = QdrantClient(
            url=os.getenv("QDRANT_URL", "http://localhost:6338"),
            api_key=settings.QDRANT_API_KEY
        )

        self._initialized = True
        logger.info(" ✅ All models ready.")

    def training_and_testing(self, file_path: str = DATA_PATH):
        TOTAL_ROWS = 19_704
        EVAL_SIZE = 300
        RANDOM_STATE = 42

        df = pd.read_json(file_path)

        # safety check
        if len(df) != TOTAL_ROWS:
            raise ValueError(
                f"Expected {TOTAL_ROWS} rows, got {len(df)}"
            )

        # shuffle first for random split
        df = df.sample(frac=1, random_state=RANDOM_STATE).reset_index(drop=True)

        # hold out eval set
        eval_df = df.iloc[:EVAL_SIZE].copy()

        # remaining training set
        train_df = df.iloc[EVAL_SIZE:].copy()

        DATA_DIR = Path(r"app\blueprints\data\medical_o1_sft_")
        TRAIN_FILE = DATA_DIR / "train.csv"
        TEST_FILE  = DATA_DIR / "test.csv"

        if TRAIN_FILE.exists() and TEST_FILE.exists():
            logger.info("✅ Train/test files already exist, loading from disk...")
            train_df = pd.read_csv(TRAIN_FILE)
            test_df  = pd.read_csv(TEST_FILE)
            return train_df, test_df
        
        # ❌ Files missing → create them once
        logger.info("📂 Files not found, creating train/test split...")
        DATA_DIR.mkdir(parents=True, exist_ok=True)


        train_df.to_csv(TRAIN_FILE, index=False)
        eval_df.to_csv(TEST_FILE,  index=False)
        logger.info(f"✅ Saved → train: {len(train_df)} rows | test: {len(eval_df)} rows")

        return train_df, eval_df

    def insert_training_data(
        self, 
        train_df: pd.DataFrame, 
        batch_size: int
    ):
        if not self.client.collection_exists(collection_name=settings.COLLECTION_NAME):
            self.client.create_collection(
                collection_name=settings.COLLECTION_NAME,
                vectors_config=models.VectorParams(
                    size=1024,
                    distance=models.Distance.COSINE
                )
            )
            logger.info(f"Collection '{settings.COLLECTION_NAME}' created.")
        else:
            logger.info(f"Collection '{settings.COLLECTION_NAME}' exists — skipping insert.")
            return
        
        total_batches = (len(train_df) + batch_size - 1) // batch_size
        inserted, failed = 0, 0
        for start in tqdm(range(0, len(train_df), batch_size),
            total=total_batches, desc="Inserting", unit="batch"):
            batch = train_df.iloc[start: start + batch_size]
            try:
                embeddings = self.embedding.encode(
                    batch["Response"].tolist(),
                    batch_size=batch_size,
                    max_length=512,
                )["dense_vecs"]

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

                self.client.upsert(collection_name=settings.COLLECTION_NAME, points=points)
                inserted += len(batch)
            except Exception as e:
                failed += len(batch)
                logger.error(f"Batch {start} failed: {e}")

        logger.info(f"✅ Inserted: {inserted} | failed: {failed}")
    
    @mlflow.trace(span_type="RETRIEVER")
    def retrieve(self, query: str) -> List[RetrievedDoc]:
        try:
            query_vector =  self.embedding.encode(
                sentences=[query], batch_size=1, max_length=512,
            )["dense_vecs"][0].tolist()

            if sum(abs(v) for v in query_vector) == 0:
                logger.error("Zero vector — embedded failed")
                return []

            results = self.client.query_points(
                collection_name=settings.COLLECTION_NAME,
                query=query_vector,
                limit=settings.TOP_K,
                with_payload=True,
            ).points

            docs = [
                RetrievedDoc(
                    question=hit.payload["question"],
                    response=hit.payload["response"],
                    vector_score=round(hit.score, 4),
                )
                for hit in results
            ]

            logger.info(
                f"Retrieved {len(docs)} | "
                f"top={docs[0].vector_score:.4f} | "
                f"low={docs[-1].vector_score:.4f}"
            )
            return docs

        except Exception as e:
            logger.error(f"Retrieval failed: {e}")
            return []

    @mlflow.trace(span_type="RERANKER")
    def re_rank(self, query: str, docs: list[RetrievedDoc]) -> list[RetrievedDoc]:
        if not docs:
            return []

        try:
            results = self.ranker.rerank(
                query=query,
                documents=[doc.response for doc in docs],
                top_n=settings.TOP_N,
                max_query_length=512,
                max_doc_length=1024,
            )

            reranked: list[RetrievedDoc] = []
            for r in results:
                original = docs[r["index"]]
                original.rerank_score = round(float(r["relevance_score"]), 4)
                reranked.append(original)

            logger.info(
                f"Reranked → top={reranked[0].rerank_score:.4f} | "
                f"low={reranked[-1].rerank_score:.4f} | "
                f"gap={reranked[0].rerank_score - reranked[-1].rerank_score:.4f}"
            )
            return reranked

        except (RuntimeError, torch.cuda.OutOfMemoryError) as e:
            logger.error(f"Reranker failed (runtime): {e}")
            return sorted(docs, key=lambda d: d.vector_score, reverse=True)[:settings.TOP_N]
    
    def retrieve_and_rerank(self, query: str) -> list[RetrievedDoc]:
        docs     = self.retrieve(query)
        reranked = self.re_rank(query, docs)
        return reranked

if __name__ == "__main__":
    pipeline = RagPipeline()


    is_exists_collection = pipeline.client.collection_exists(
        collection_name=settings.COLLECTION_NAME
    )

    print(f" ✅ collection name: {settings.COLLECTION_NAME} : ({is_exists_collection})")

    train_df, eval_df = pipeline.training_and_testing()
    first_question = eval_df["Question"][0]
    second_question = "my patient has diabetes what should i give him first"


    print(f"✅ first_question: {second_question}")
    result = pipeline.retrieve_and_rerank(query=second_question)

    print(f"✅ result:\n{result}")

