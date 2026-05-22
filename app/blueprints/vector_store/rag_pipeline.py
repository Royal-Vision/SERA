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
COLLECTION_NAME = "medical_o1_sft"
TOTAL_ROWS      = 19_704
EVAL_SIZE       = 300      # held out — never in Qdrant
RAGAS_SAMPLES   = 50       # costly LLM eval subset
TOP_K           = 20       # retrieve from Qdrant
TOP_N           = 5        # keep after reranking → sent to LLM


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

    def training_and_testing(self, file_path: str):
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

        return train_df, eval_df

    def insert_training_data(
        self, 
        train_df: pd.DataFrame, 
        batch_size: int
    ):
        if not self.client.collection_exists(collection_name=COLLECTION_NAME):
            self.client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=models.VectorParams(
                    size=1024,
                    distance=models.Distance.COSINE
                )
            )
            logger.info(f"Collection '{COLLECTION_NAME}' created.")
        else:
            logger.info(f"Collection '{COLLECTION_NAME}' exists — skipping insert.")
            return
        
        total_batches = (len(train_df) + batch_size - 1) // batch_size
        inserted, failed = 0, 0
        for start in tqdm(range(0, len(train_df), batch_size),
            total=total_batches, desc="Inserting", unit="batch"):
            batch = train_df.iloc[start: start + batch_size]
            try:
                embeddings = self.embedder.encode(
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

                self.client.upsert(collection_name=COLLECTION_NAME, points=points)
                inserted += len(batch)
            except Exception as e:
                failed += len(batch)
                logger.error(f"Batch {start} failed: {e}")

        logger.info(f"✅ Inserted: {inserted} | failed: {failed}")
    
    def retrieve(self, query: str) -> List[RetrievedDoc]:
        try:
            query_vector =  self.embedding.encode(
                sentences=[query], batch_size=1, max_length=512,
            )["dense_vecs"][0].tolist()

            if sum(abs(v) for v in query_vector) == 0:
                logger.error("Zero vector — embedded failed")
                return []

            results = self.client.query_points(
                collection_name=COLLECTION_NAME,
                query=query_vector,
                limit=TOP_K,
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


    def re_rank(self, query: str, docs: list[RetrievedDoc]) -> list[RetrievedDoc]:
        if not docs:
            return []
    
        try:
            pairs = [[query, doc.response] for doc in docs]
    
            with torch.no_grad():
                scores = self.ranker.compute_score(
                    pairs,
                    max_length=1024,
                    doc_type="text",
                )
    
            for doc, score in zip(docs, scores):
                doc.rerank_score = round(float(score), 4)
    
            reranked = sorted(docs, key=lambda d: d.rerank_score, reverse=True)[:TOP_N]
    
            logger.info(
                f"Reranked → top={reranked[0].rerank_score:.4f} | "
                f"low={reranked[-1].rerank_score:.4f} | "
                f"gap={reranked[0].rerank_score - reranked[-1].rerank_score:.4f}"
            )
            return reranked
    
        except Exception as e:
            logger.error(f"Reranker failed: {e}")
            return sorted(docs, key=lambda d: d.vector_score, reverse=True)[:TOP_N]
    
    def retrieve_and_rerank(self, query: str) -> list[RetrievedDoc]:
        docs     = self.retrieve(query)
        reranked = self.re_rank(query, docs)
        return reranked

