# SERA AI — Software Requirements Specification (SRS)

**Status:** Draft v1
**Last updated:** 2026-05-25
**Owner:** ahmed-mohamed-77

---

## 1. Executive summary

SERA AI is intended to be a backend API for a medical-domain Retrieval Augmented Generation (RAG) system. Current state: **a lot of infrastructure scaffolding, but no product surface yet.** The FastAPI app boots with only `/health` and `/metrics`. There are zero database tables, zero request/response schemas, zero domain routes, no authentication, and several broken or dead modules.

A working RAG retrieval+rerank pipeline exists ([`vector_store/rag_pipeline.py`](app/blueprints/vector_store/rag_pipeline.py)) and a working evaluation harness exists ([`rag_eval/`](app/blueprints/rag_eval/)) — but they aren't exposed via the API.

This document inventories the current state, identifies broken/dead code, and lays out the minimum work to ship a real product.

---

## 2. Current state inventory

### 2.1 Files that are working and wired

| File | Status | What it does |
|---|---|---|
| `main.py` | ✅ runs | FastAPI app with `/health` + `/metrics` only |
| `app/configs/config.py` | ✅ used | Pydantic settings — read from `.env` |
| `app/configs/logger.py` | ✅ used | Singleton rotating-file logger |
| `app/blueprints/utilities/metrics.py` | ✅ wired | Prometheus counters/histograms — started in lifespan |
| `app/blueprints/utilities/mlflow_tracker.py` | ✅ used | MLflow connection + autolog (singleton) |
| `app/blueprints/utilities/llm_trace.py` | ✅ used | Back-compat re-export shim |
| `app/blueprints/vector_store/rag_pipeline.py` | ✅ used by eval | Retrieve + rerank with `@mlflow.trace` decorators |
| `app/blueprints/rag_eval/dataset.py` | ✅ used | Frozen eval dataset (`DatasetVersion`) |
| `app/blueprints/rag_eval/mlflow_eval.py` | ✅ used | Eval runner (`RagEvalMLflow.eval_run` / `run_evaluation`) |
| `app/blueprints/rag_eval/tests/*.py` | ✅ used | pytest suite — 15 unit + 6 integration |
| `app/blueprints/models/rewriter_llm.py` | ⚠️ standalone | Qwen3 query rewriter — runs at import, not wired |
| `app/blueprints/prompts/rewriter_prompt.py` | ⚠️ standalone | Pydantic schema for rewriter output |

### 2.2 Files that are EMPTY (1 line or 0 bytes) — dead placeholders

These exist as artifacts of earlier plans that we collapsed when adopting `mlflow.genai.evaluate`. They should be **deleted**.

| File | Why dead |
|---|---|
| `app/blueprints/models/embedding_model.py` | Replaced by inline use of BGE-M3 in RagPipeline |
| `app/blueprints/rag_eval/runner.py` | Replaced by `RagEvalMLflow.run_evaluation` |
| `app/blueprints/rag_eval/pipeline.py` | Replaced by `RagEvalMLflow.eval_run` + `run_evaluation` |
| `app/blueprints/rag_eval/generator.py` | Tier 2 placeholder — not yet needed |
| `app/blueprints/rag_eval/ragas_judge.py` | Replaced by `mlflow.genai.scorers` (built-in) |
| `app/blueprints/rag_eval/retrieval_metrics.py` | Replaced by MLflow built-in retrieval scorers |

### 2.3 Files that are BROKEN (import-time crash or stale references)

| File | Problem | Action |
|---|---|---|
| `app/configs/redis.py` | References `settings.REDIS_HOST` and `REDIS_PORT` — neither defined in `config.py` (only `REDIS_PASSWORD`, `REDIS_URL`) | Rewrite to use `REDIS_URL` |
| `app/configs/minio_bucket.py` | References `settings.MINIO_*` — defined as `S3_*` in config. Plus **module-level `minio_client = MinioClient()` crashes on import** when settings missing | Rewrite to use `S3_*` + lazy init |
| `app/blueprints/utilities/PII.py` | Runs scrubber demo code at module top level (lines 24–46) on every import. **Side-effect on import.** | Move test code into `if __name__ == "__main__"` block |
| `app/blueprints/vector_store/client.py` | Setup script disguised as module — runs `delete_collection()`, `load_and_insert()`, `create_collection()` at import time | Delete entirely — replaced by `dataset.DatasetVersion.evaluation_collection` |
| `app/blueprints/vector_store/retrive.py` | Older `retrieve()` helper, superseded by `RagPipeline.retrieve` | Delete — RagPipeline is now the single source of truth |
| `app/blueprints/vector_store/parser.py` | One-off PDF parsing script — runs heavy model load + processing at import | Move to `scripts/` and guard with `__main__` |

### 2.4 What's defined in config but NOT used anywhere

These environment variables are declared in [`config.py`](app/configs/config.py) but never referenced by any wired code path:

- `JWT_SECRET_KEY`, `JWT_ALGORITHM`, `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` — no auth implemented
- `ALLOWED_ORIGINS` — no CORS middleware in `main.py`
- `GOOGLE_CLIENT_ID/SECRET`, `FACEBOOK_CLIENT_ID/SECRET`, `FACEBOOK_REDIRECT_URI` — no OAuth flow
- `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY` — never read
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` — no email service
- `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET`, `S3_SECURE` — MinIO module is broken
- `POSTGRES_USER/PASSWORD/DB`, `DATABASE_URL`, `ALEMBIC_DATABASE_URL` — DB never connected
- `REDIS_PASSWORD`, `REDIS_URL` — Redis never connected

The RAG-specific settings (`COLLECTION_NAME`, `TOTAL_ROWS`, `EVAL_SIZE`, `RAGAS_SAMPLES`, `TOP_K`, `TOP_N`, `RANDOM_STATE`) are used by `dataset.py` and `rag_pipeline.py` — these are fine.

### 2.5 Critical missing pieces

The codebase has **zero** of:

| Missing | Impact |
|---|---|
| **SQLModel table definitions** | DB has no schema. `Database.create_tables()` would create nothing. |
| **Pydantic request/response schemas** | No API contracts. Every endpoint would have to invent its own. |
| **API routers** | Only `/health` and `/metrics` exist. No domain endpoints. |
| **Authentication / authorization** | JWT settings exist but no token issuing, no protected routes. |
| **CORS middleware** | `ALLOWED_ORIGINS` is set but never applied. Browser clients can't call the API. |
| **DB connection on startup** | `Database` class exists but `lifespan()` doesn't call `db.connect()`. |
| **Alembic migrations** | No `alembic/` directory. `ALEMBIC_DATABASE_URL` is set but unused. |
| **README / setup docs** | No project root README. New devs have no starting point. |

---

## 3. Required functionality

What needs to be built for SERA AI to be an actual deployable product.

### 3.1 Core API surface (REST endpoints to expose)

These are the minimum routes needed to make the existing RAG pipeline + eval usable from outside:

| Method | Path | Purpose | Auth |
|---|---|---|---|
| `POST` | `/v1/auth/signup` | Email/password registration | public |
| `POST` | `/v1/auth/login` | Issue JWT | public |
| `POST` | `/v1/auth/refresh` | Refresh access token | refresh JWT |
| `GET`  | `/v1/auth/me` | Current user info | access JWT |
| `POST` | `/v1/rag/query` | Run retrieve+rerank, return top-N docs | access JWT |
| `POST` | `/v1/rag/answer` | Full RAG (retrieve+rerank+LLM generation) | access JWT |
| `POST` | `/v1/rag/feedback` | User feedback on a previous answer | access JWT |
| `GET`  | `/v1/rag/sessions` | List user's past sessions | access JWT |
| `GET`  | `/v1/rag/sessions/{id}` | Single session detail | access JWT |
| `POST` | `/v1/eval/runs` | Trigger an eval run (background task) | admin JWT |
| `GET`  | `/v1/eval/runs` | List eval runs from MLflow | admin JWT |
| `GET`  | `/v1/eval/runs/{run_id}` | Single eval run detail | admin JWT |
| `POST` | `/v1/docs/upload` | Upload PDF / doc for indexing | admin JWT |
| `GET`  | `/v1/docs` | List indexed documents | admin JWT |
| `DELETE` | `/v1/docs/{id}` | Remove document from index | admin JWT |

### 3.2 Database tables (SQLModel)

| Table | Purpose | Key fields |
|---|---|---|
| `users` | Account records | `id`, `email`, `password_hash`, `role`, `created_at` |
| `sessions` | Conversation containers | `id`, `user_id`, `title`, `created_at` |
| `messages` | Q/A turn pairs | `id`, `session_id`, `role`, `content`, `mlflow_trace_id`, `created_at` |
| `feedback` | User ratings on messages | `id`, `message_id`, `user_id`, `rating`, `comment`, `created_at` |
| `documents` | Indexed corpus metadata | `id`, `title`, `source_uri`, `qdrant_point_count`, `uploaded_by`, `created_at` |
| `eval_runs` | Cache of MLflow eval runs for the UI | `mlflow_run_id` (PK), `run_name`, `status`, `started_at`, `metrics_json` |

### 3.3 Pydantic schemas (request/response)

For each route in 3.1, define:
- `XxxCreate` (request body)
- `XxxResponse` (response body)
- `XxxUpdate` (for PATCH-style updates)

Group under `app/schemas/auth.py`, `app/schemas/rag.py`, `app/schemas/eval.py`, `app/schemas/docs.py`.

### 3.4 Infrastructure to wire (already declared, not connected)

| Component | Action |
|---|---|
| PostgreSQL | Add `db.connect(settings.DATABASE_URL)` in `main.lifespan()` |
| Redis | Fix `redis.py` config refs + add `redis_client.connect()` in lifespan |
| MinIO | Fix `minio_bucket.py` config refs + lazy-init the client |
| CORS | Add `CORSMiddleware` to FastAPI app using `settings.cors_origins` |
| Auth | Implement JWT issuing + `Depends(get_current_user)` dependency |

---

## 4. Proposed target architecture

```
SERA AI Backend
├── main.py                          ← FastAPI entry + lifespan (DB, Redis, MinIO)
├── app/
│   ├── configs/
│   │   ├── config.py                ← settings (cleanup: drop unused vars)
│   │   ├── db.py                    ← wire into lifespan
│   │   ├── redis.py                 ← FIX broken refs, wire into lifespan
│   │   ├── minio_bucket.py          ← FIX broken refs, wire into lifespan
│   │   └── logger.py                ← keep
│   │
│   ├── models/                      ← NEW: SQLModel table definitions
│   │   ├── user.py
│   │   ├── session.py
│   │   ├── message.py
│   │   ├── feedback.py
│   │   ├── document.py
│   │   └── eval_run.py
│   │
│   ├── schemas/                     ← NEW: Pydantic request/response schemas
│   │   ├── auth.py
│   │   ├── rag.py
│   │   ├── docs.py
│   │   └── eval.py
│   │
│   ├── routes/                      ← NEW: API routers
│   │   ├── auth.py
│   │   ├── rag.py
│   │   ├── docs.py
│   │   └── eval.py
│   │
│   ├── services/                    ← NEW: business logic layer
│   │   ├── auth_service.py
│   │   ├── rag_service.py
│   │   ├── docs_service.py
│   │   └── eval_service.py
│   │
│   ├── dependencies.py              ← NEW: get_current_user, get_db, get_redis
│   │
│   └── blueprints/
│       ├── utilities/               ← keep all (metrics, mlflow_tracker, llm_trace)
│       │   └── PII.py               ← FIX: move demo into __main__
│       ├── vector_store/
│       │   ├── rag_pipeline.py      ← keep — used by rag_service
│       │   ├── client.py            ← DELETE
│       │   ├── retrive.py           ← DELETE
│       │   └── parser.py            ← move to scripts/
│       ├── models/                  ← DELETE empty file
│       ├── prompts/                 ← keep
│       └── rag_eval/                ← keep — used by eval_service
│           ├── dataset.py
│           ├── mlflow_eval.py
│           ├── runner.py            ← DELETE (empty)
│           ├── pipeline.py          ← DELETE (empty)
│           ├── generator.py         ← DELETE (until Tier 2)
│           ├── ragas_judge.py       ← DELETE (replaced by mlflow.genai.scorers)
│           ├── retrieval_metrics.py ← DELETE (replaced by built-in scorers)
│           └── tests/               ← keep — expand for new code
│
├── alembic/                         ← NEW: migrations
│   ├── env.py
│   └── versions/
│
├── scripts/                         ← NEW: one-off tools
│   ├── parse_pdf.py                 ← from blueprints/vector_store/parser.py
│   └── reindex_corpus.py
│
└── docs/
    ├── SRS.md                       ← this file
    ├── API.md                       ← endpoint reference (auto-gen from OpenAPI)
    └── ARCHITECTURE.md              ← system diagrams
```

---

## 5. Roadmap (prioritized)

### Phase 0 — Cleanup (1 day, no new features)

**Goal:** make `python -m app` import cleanly with no broken modules, no side-effect imports, no dead files.

- [ ] Delete 6 empty files in `app/blueprints/rag_eval/` (runner, pipeline, generator, ragas_judge, retrieval_metrics, models/embedding_model)
- [ ] Delete `app/blueprints/vector_store/client.py` (one-shot setup script)
- [ ] Delete `app/blueprints/vector_store/retrive.py` (superseded by RagPipeline)
- [ ] Move `app/blueprints/vector_store/parser.py` → `scripts/parse_pdf.py`
- [ ] Fix `app/blueprints/utilities/PII.py` (move demo code into `__main__`)
- [ ] Fix `app/configs/redis.py` (use `REDIS_URL`, drop fictional `REDIS_HOST`/`PORT`)
- [ ] Fix `app/configs/minio_bucket.py` (use `S3_*` settings, lazy-init)
- [ ] Remove unused settings from `config.py` (Facebook OAuth, Anthropic, Google API, SMTP — comment-out for now, delete once you're sure)
- [ ] Untrack `__pycache__/` + `log/` from git history
- [ ] Add a project root `README.md` with setup + run instructions

### Phase 1 — Foundation (2-3 days)

**Goal:** infrastructure layer is wired, tested, and observable.

- [ ] Wire `db.connect()` into `main.lifespan()` — fail-fast on bad DB URL
- [ ] Wire `redis_client.connect()` into lifespan
- [ ] Wire `minio_client.ensure_bucket()` into lifespan
- [ ] Add CORS middleware using `settings.cors_origins`
- [ ] Add Alembic — init + first empty migration
- [ ] Define the 6 SQLModel tables (see §3.2)
- [ ] Generate Alembic migration for the 6 tables
- [ ] Add integration test verifying lifespan starts all 3 services

### Phase 2 — Auth (1-2 days)

- [ ] `app/services/auth_service.py` — bcrypt hashing, JWT issue/verify
- [ ] `app/dependencies.py` — `get_db()`, `get_current_user()`, `get_admin_user()`
- [ ] `app/schemas/auth.py` — UserCreate, UserResponse, TokenResponse, RefreshRequest
- [ ] `app/routes/auth.py` — signup / login / refresh / me
- [ ] Integration tests for each auth route

### Phase 3 — RAG API surface (2-3 days)

- [ ] `app/schemas/rag.py` — RagQuery, RagAnswer, RagFeedback, SessionResponse
- [ ] `app/services/rag_service.py` — wraps `RagPipeline.retrieve_and_rerank` + persists sessions/messages
- [ ] `app/routes/rag.py` — query / answer / feedback / sessions
- [ ] Integration tests

### Phase 4 — Docs ingestion API (2 days)

- [ ] `app/schemas/docs.py`
- [ ] `app/services/docs_service.py` — upload to MinIO, chunk, embed, insert into Qdrant
- [ ] `app/routes/docs.py`
- [ ] Move PDF parser from `scripts/parse_pdf.py` into the docs service as a strategy

### Phase 5 — Eval API + admin (1-2 days)

- [ ] `app/schemas/eval.py`
- [ ] `app/services/eval_service.py` — wraps `RagEvalMLflow.run_evaluation`, persists in `eval_runs` table
- [ ] `app/routes/eval.py` — POST run (background), GET list/detail (queries MLflow tracking server)
- [ ] Admin role check on all routes

### Phase 6 — Tier 2 RAG generation (already in todo list)

- [ ] Generator LLM (Qwen3-4B or external)
- [ ] Add `Faithfulness`, `AnswerCorrectness`, `Fluency`, `RelevanceToQuery` scorers
- [ ] Update `predict_fn` to call generator
- [ ] First Tier 2 baseline run in MLflow

### Phase 7 — Production hardening (already in todo list)

- [ ] Cost caps for eval runs
- [ ] Regression gate (eval fails CI if metrics drop ≥5%)
- [ ] Embedding drift monitor
- [ ] Rate limiting on RAG endpoints
- [ ] Request/response logging with PII scrubbing

---

## 6. Cleanup task summary (Phase 0 detail)

Each item below should be its own small PR — easy to review, easy to revert.

| # | File | Action | Risk |
|---|---|---|---|
| 1 | `app/blueprints/rag_eval/runner.py` | `git rm` | None — empty file |
| 2 | `app/blueprints/rag_eval/pipeline.py` | `git rm` | None — empty file |
| 3 | `app/blueprints/rag_eval/generator.py` | `git rm` | None — empty file |
| 4 | `app/blueprints/rag_eval/ragas_judge.py` | `git rm` | None — empty file |
| 5 | `app/blueprints/rag_eval/retrieval_metrics.py` | `git rm` | None — empty file |
| 6 | `app/blueprints/models/embedding_model.py` | `git rm` | None — empty file |
| 7 | `app/blueprints/vector_store/client.py` | `git rm` | None — one-shot script not imported anywhere |
| 8 | `app/blueprints/vector_store/retrive.py` | `git rm` | None — superseded |
| 9 | `app/blueprints/vector_store/parser.py` | move to `scripts/parse_pdf.py` + guard with `__main__` | Low — heavy model load was running on import |
| 10 | `app/blueprints/utilities/PII.py` | wrap demo (lines 24–46) in `if __name__ == "__main__"` | Low — slow import currently |
| 11 | `app/configs/redis.py` | rewrite to use `settings.REDIS_URL` | Low — module was broken anyway |
| 12 | `app/configs/minio_bucket.py` | rewrite to use `settings.S3_*` + remove module-level `MinioClient()` | Low — broken anyway |
| 13 | `app/configs/config.py` | comment-out unused settings (Facebook, Anthropic, Google API, SMTP) | Low |
| 14 | `git rm --cached log/ __pycache__/` | untrack noise files | None |
| 15 | `README.md` | create with setup + run | None |
| 16 | `mlflow.db` (root) | `git rm` | None — leftover SQLite from earlier testing, not referenced |

After Phase 0, the diff should reduce the file count by ~10 files and the codebase should import cleanly with **zero** side effects.

---

## 7. Open questions / decisions needed

These are choices that should be locked in before Phase 1 starts.

| Question | Options | Recommendation |
|---|---|---|
| Single tenant or multi-tenant? | (a) one global Qdrant collection, (b) per-user collections | (a) for v1 — simpler |
| Document chunking strategy | (a) fixed-size tokens, (b) semantic, (c) per-document choice | (b) for medical content — semantic |
| Generator LLM for Tier 2 | (a) local Qwen3-4B, (b) gpt-4o-mini, (c) Claude Haiku | Start (a), add (b) as fallback |
| Session retention | days / forever | 90 days, then archive |
| Feedback granularity | 5-star / thumbs / detailed | thumbs + free-text comment |
| Eval cadence | manual / nightly CI / on every PR | nightly CI + on-demand admin trigger |
| OAuth providers | Google only, or Google + Facebook | Google only — Facebook OAuth code already stale |

---

## 8. Acceptance criteria (definition of "done" for v1)

The product is considered v1-ready when:

1. ✅ A new user can sign up, log in, and ask a RAG query via the API
2. ✅ Their session + messages persist in PostgreSQL
3. ✅ MLflow shows traces for every RAG query (auto-instrumented)
4. ✅ Admin can trigger an eval run via the API and see results in MLflow UI
5. ✅ Documents can be uploaded, indexed, and retrieved
6. ✅ `pytest` passes with ≥90% coverage on `app/services/` and `app/routes/`
7. ✅ `docker compose up` boots cleanly with all 4 services healthy
8. ✅ `README.md` documents setup, run, test, deploy in ≤ 1 page

---

## 9. Out of scope for v1

Explicit non-goals (so we don't accidentally build them):

- Streaming responses (SSE / WebSocket)
- Multi-modal input (images, audio)
- Fine-tuning the embedder or reranker
- Production-grade rate limiting (basic only)
- Multi-region deployment
- Mobile push notifications
- Real-time collaboration / shared sessions

These return in v2+ planning.

---

## 10. References

- **MLflow tracking server:** https://mlflow.ghoniem.online
- **Qdrant (production):** https://qdrant.ghoniem.online
- **Source dataset:** `app/blueprints/data/medical_o1_sft.json` (19,704 Q/A pairs)
- **Frozen eval set:** `app/blueprints/data/eval_gold_v1.parquet` (300 rows, sha256 in `.meta.json`)
- **Eval implementation guide:** in conversation history (Claude session)
- **RAGAS docs:** https://docs.ragas.io/en/stable/
- **MLflow GenAI eval:** https://mlflow.org/docs/latest/genai/eval-monitor/
