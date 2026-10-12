# Intelligent Document Processing & RAG Platform

> **Status**: 🚧 In Development - Phase 6 complete (Embeddings and semantic search). Hybrid search, reranking and RAG answers are not implemented yet.

## Overview

A production-oriented intelligent document processing and Retrieval-Augmented Generation (RAG) platform demonstrating professional-level software engineering practices. This is a portfolio project designed to showcase expertise in backend development, AI/ML integration, and system architecture.

**This is NOT a tutorial project or basic "chat with PDF" application.**

## Problem Statement

Organizations need to extract insights from large document collections. Traditional search provides keyword matching but misses semantic meaning. Pure LLM approaches hallucinate and lack grounding. This platform combines:

- **Semantic understanding** via vector embeddings
- **Precise retrieval** through hybrid search
- **Grounded generation** with citation-backed RAG
- **Quality measurement** through systematic evaluation

## Key Features

### Implemented
Infrastructure, authentication, secure document management, asynchronous text extraction, structure-aware chunking, multilingual embeddings and owner-scoped semantic search; documents are processed into traceable chunks and vectors that can be searched in Bangla, English and mixed text, but no answers are generated yet (no hybrid search, reranking or RAG).

- Local multilingual embeddings (`intfloat/multilingual-e5-small`: 384 dimensions, MIT, 94 documented languages including Bengali, CPU-friendly, about 0.5 GB), behind a provider interface; the model revision is pinned, weights load from safetensors only, and nothing is downloaded into the image
- A versioned vector store in PostgreSQL/pgvector (cosine distance): every vector records the exact model configuration it came from, belongs to a chunk of the same document and to that document's owner (enforced by foreign keys), and a model change requires an explicit re-index instead of silently mixing vectors
- A dedicated embedding worker with batched, idempotent, time-bounded jobs, crash recovery and retries; `ready` now means *searchable* (every chunk has a vector)
- `POST /api/v1/search/semantic`: authenticated, rate-limited, owner-scoped search returning chunk text with page, heading, section and score; no user can reach another user's documents
- A small version-controlled evaluation dataset (Bangla, English, mixed, cross-lingual, confusable and unanswerable queries) with Recall@k, MRR, nDCG@k and latency/throughput measurement

- Deterministic, versioned chunking that never crosses a page or heading boundary: each chunk is an exact slice of its source with page number, heading path and character offsets, bounded in size, with overlap only inside oversized paragraphs
- Bangla-safe splitting (Bengali danda sentence ends, no cut inside a conjunct or ZWJ/ZWNJ sequence), Markdown tables and code fences kept whole, idempotent re-chunking and a `chunked` status that keeps `ready` reserved for searchable documents
- `GET /api/v1/documents/{id}/chunks` for owner-scoped inspection of how a document was split

- Background processing (Celery + Redis) of PDF, DOCX, TXT and Markdown into normalised text with page and heading locations
- Bangla, English and mixed-script documents are preserved (Unicode-safe normalisation, ZWJ/ZWNJ kept, no translation)
- Classified, safe failure reasons, bounded retries, manual retry, and automatic recovery of stuck work

- Upload PDF, DOCX, TXT and Markdown with content validation, checksums and per-user duplicate detection
- Collections, filtered and paginated listing, metadata, streaming download and deletion, all owner-scoped

- Registration, login, refresh-token rotation with reuse detection, logout and `GET /api/v1/auth/me`
- Role-based access control (user, admin) enforced server-side; rate-limited auth endpoints

- FastAPI service with liveness/readiness health endpoints and versioned `/api/v1` routing
- PostgreSQL 16 + pgvector, Redis and a Celery worker, all running under Docker Compose with health checks
- Alembic migrations, structured JSON logging with request IDs, consistent error responses
- React + TypeScript + Vite shell that displays backend readiness
- Test, lint, type-check and CI foundations

### Planned

#### Document Management
- Upload documents (PDF, DOCX, TXT, Markdown)
- Organize documents into collections
- Track processing status
- Version control for documents

#### Intelligent Search
- **Semantic search** - Find conceptually similar content
- **Keyword search** - PostgreSQL full-text search
- **Hybrid search** - Combined semantic + keyword
- **Reranking** - Cross-encoder relevance scoring
- **Metadata filtering** - Filter by document attributes

#### RAG Capabilities
- Question answering with citations
- Conversation history management
- Context window optimization
- Multi-document reasoning

#### Evaluation
- Retrieval quality metrics (Recall@K, MRR)
- Answer quality assessment
- Citation correctness verification
- Automated evaluation pipelines

#### Security & Observability
- JWT-based authentication
- Role-based authorization
- Structured logging with request tracing
- Processing pipeline visibility

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                   React Frontend                         │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│                  FastAPI Application                     │
│  ┌──────────────┬───────────────┬──────────────────┐    │
│  │  Auth Layer  │  API Routes   │  Service Layer   │    │
│  └──────────────┴───────────────┴──────────────────┘    │
└──────────┬─────────────────────────┬────────────────────┘
           │                         │
┌──────────▼──────────┐    ┌─────────▼─────────────────────┐
│ PostgreSQL+pgvector │    │            Redis              │
│  - Relational data  │    │  - Cache   - Sessions         │
│  - Vector search    │    │  - Celery broker              │
└─────────────────────┘    └───────────────────────────────┘
                                      │
                         ┌────────────▼────────────┐
                         │     Celery Workers      │
                         │  - Document processing  │
                         │  - Embedding generation │
                         │  - Evaluation jobs      │
                         └─────────────────────────┘
```

**Pattern**: Modular Monolith + Asynchronous Worker Architecture

## Technology Stack

| Category | Technology | Purpose |
|----------|-----------|---------|
| **Backend** | Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2.x | API server, validation, ORM |
| **Database** | PostgreSQL 16, pgvector | Relational + vector storage |
| **Cache/Broker** | Redis | Session store, message broker, cache |
| **Task Queue** | Celery | Async document processing |
| **AI/ML** | sentence-transformers, Hugging Face, PyTorch | Embeddings, LLM integration |
| **Document Processing** | PyMuPDF, python-docx | PDF, DOCX parsing |
| **Frontend** | React, TypeScript, Vite | User interface |
| **Infrastructure** | Docker, Docker Compose | Containerization |
| **Testing** | pytest, pytest-asyncio, httpx | Test framework |

## Quick Start

Prerequisites: Docker Desktop (Compose v2). Nothing else is needed to run the stack.

```bash
git clone https://github.com/Jobayer-hasan-rifat/Rag.git
cd Rag

# 1. Create your local environment file and replace the placeholder secrets
cp .env.example .env

# 2. Build and start everything (migrations run automatically)
docker compose up -d --build

# 3. Check status; every service should become "healthy"
docker compose ps
```

| Service | URL |
|---------|-----|
| Frontend | http://localhost:5173 |
| API docs (Swagger) | http://localhost:8000/docs |
| Readiness probe | http://localhost:8000/health/ready |

**First start and the embedding model.** The `embedding_worker` service downloads the embedding model (about 0.5 GB of weights, pinned to a commit hash) into the shared `model_cache` volume the first time it starts, which takes about a minute on a normal connection; documents wait in `chunked` until it is ready. Everything runs on the CPU (the Docker image uses the CPU build of PyTorch); a GPU is optional (`EMBEDDING_DEVICE=auto` uses CUDA or Apple MPS when present). The embedding worker uses about 0.9 GB of memory with the default model (limit 3 GB) and the API loads its own copy for query embedding on the first search. To fetch the model ahead of time: `docker compose run --rm embedding_worker python -m app.embeddings.download`. Changing `EMBEDDING_MODEL`, its revision or the prefixes means re-embedding: run `docker compose exec embedding_worker celery -A app.workers.celery_app:celery_app call app.workers.embedding_tasks.reindex_embeddings`. All settings are listed in `.env.example`.

Try a search once a document is `ready` (replace the token):

```bash
curl -s -X POST http://localhost:8000/api/v1/search/semantic \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"query": "ইলিশ ধরা কবে নিষিদ্ধ?", "top_k": 5}'
```

`score` is the cosine similarity between query and chunk (a ranking signal, not a probability); see the [API specification](docs/05_API_SPECIFICATION.md).

PostgreSQL and Redis are published on `127.0.0.1` only. If a host port is taken (for example Windows reserves 6379 on some machines), change `POSTGRES_HOST_PORT` / `REDIS_HOST_PORT` in `.env`.

### Development commands

| Task | Command |
|------|---------|
| Start / rebuild stack | `docker compose up -d --build` |
| Stop stack (keep data) | `docker compose down` |
| Stop and delete data volumes | `docker compose down -v` |
| View logs | `docker compose logs -f backend celery_worker` |
| Run migrations manually | `docker compose run --rm migrate alembic upgrade head` |
| Roll back one migration | `docker compose run --rm migrate alembic downgrade -1` |
| Start the worker only | `docker compose up -d celery_worker celery_beat` |

Working on the backend on the host (Python 3.12+; `make` is optional, the raw commands work everywhere):

```bash
cd backend
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt                    # make install
docker compose up -d postgres redis                    # infrastructure only
alembic upgrade head                                   # make migrate
uvicorn app.main:get_app --factory --reload            # run the API
celery -A app.workers.celery_app:celery_app worker -l INFO   # run a worker
python -m ruff check .                                 # make lint
python -m black .                                      # make format
python -m mypy                                         # make typecheck
python -m pytest                                       # make test
```

Frontend on the host (Node 22):

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173
npm run typecheck && npm run build
```

## Project Structure

```
/
├── backend/             # FastAPI application, Celery workers, Alembic migrations, tests
│   ├── app/             # api, services, db, workers, parsers, storage, observability, schemas
│   ├── alembic/         # Database migrations
│   └── tests/           # unit, api, integration
├── frontend/            # React + TypeScript + Vite application shell
├── docs/                # Engineering documentation
├── docker-compose.yml   # Development environment
└── .github/workflows/   # CI
```

## Development Status

| Phase | Description | Status |
|-------|-------------|--------|
| 0 | Foundation & Architecture | ✅ Complete |
| 1 | Infrastructure | ✅ Complete |
| 2 | Authentication | ✅ Complete |
| 3 | Document Management | ✅ Complete |
| 4 | Document Processing | ✅ Complete |
| 5 | Intelligent Chunking | ✅ Complete |
| 6 | Embeddings & Semantic Search | ✅ Complete |
| 7 | Hybrid Retrieval | ⏳ Planned |
| 8 | Reranking | ⏳ Planned |
| 9 | RAG | ⏳ Planned |
| 10 | Conversations | ⏳ Planned |
| 11 | Versioning & Access Control | ⏳ Planned |
| 12 | Evaluation | ⏳ Planned |
| 13 | Frontend | ⏳ Planned |
| 14 | Security Hardening | ⏳ Planned |
| 15 | Performance & Benchmarking | ⏳ Planned |
| 16 | CI/CD & Deployment | ⏳ Planned |
| 17 | Final Audit & Portfolio Prep | ⏳ Planned |

Numbering follows the order in which phases were executed; planned phases may be reordered.

## Documentation

Published engineering documentation is in the `docs/` directory:

- [Architecture](docs/02_ARCHITECTURE.md)
- [Technology Stack](docs/03_TECH_STACK.md)
- [Database Schema](docs/04_DATABASE_SCHEMA.md)
- [API Specification](docs/05_API_SPECIFICATION.md)
- [Security Overview](docs/06_SECURITY.md)
- [Testing Strategy](docs/11_TESTING_STRATEGY.md)
- [Design Decisions](docs/18_DECISION_LOG.md)
- [Changelog](CHANGELOG.md)

## Testing

Integration tests run against real PostgreSQL (pgvector) and Redis containers started by
Testcontainers, so they do not depend on your local setup. Docker must be running.

```bash
cd backend
python -m pytest -m "not integration"   # fast tests, no Docker needed
python -m pytest -m integration         # real PostgreSQL, Redis and Celery worker
python -m pytest --cov                  # everything, with coverage
```

Current state (Phase 6): 1112 tests passing and 12 skipped on Windows (11 opt-in tests that load the real embedding model, run with `RAG_REAL_MODEL_TESTS=1`, and 1 POSIX-only test), about 99% line coverage of `app/`. Ordinary runs use a deterministic embedding provider and never download a model. See the [Testing Strategy](docs/11_TESTING_STRATEGY.md).

## Evaluation

This platform includes systematic evaluation of RAG quality:

### Retrieval Metrics
- Recall@K, Precision@K, Hit@K, MRR

### Generation Metrics
- Answer relevance, groundedness, factual consistency

### Citation Metrics
- Citation precision, recall, correctness

*The full RAG evaluation framework (generation and citation metrics) is planned.*

### Retrieval evaluation (implemented in Phase 6)

`backend/evaluation/` holds a small synthetic dataset (48 passages in English, Bangla and mixed documents; 52 queries including cross-lingual, confusable-wording, punctuation/Unicode and unanswerable ones), metric code (Recall@k, MRR, nDCG@k, answerability AUROC) and runners: `python -m evaluation.retrieval_eval --mode database` evaluates the real pipeline end to end; `python -m evaluation.compare_models` compares candidate models. Measured with the default model on CPU:

| Metric | Value |
|--------|------:|
| Recall@1 / @3 / @10 | 0.891 / 1.000 / 1.000 |
| MRR@10 / nDCG@10 | 0.942 / 0.957 |
| Bangla queries (12) Recall@1 / @3 | 0.917 / 1.000 |
| Cross-lingual queries (4) Recall@1 | 0.750 |

The same dataset shows why the model was chosen: a multilingual model that does not list Bengali among its languages reached Bangla Recall@1 of 0.42 against 0.92 here (see DEC-037 in the [decision log](docs/18_DECISION_LOG.md)). **Read these numbers carefully:** the dataset is small (one query is about 2 points), synthetic, written by one author, uses short passages, and the model was selected on it, so the results show that the pipeline separates these passages, not that Bangla retrieval is strong in general. A larger evaluation on real documents is planned.

## Security

Security is a first-class concern in this project:

- **Authentication**: short-lived JWT access tokens, rotating hashed refresh tokens with reuse detection, bcrypt password hashing, Redis-backed rate limiting
- **Authorization**: Role-based access control (RBAC)
- **Input Validation**: Pydantic schemas, file validation
- **Document Security**: all uploads treated as untrusted: size caps, signature and structure checks, generated storage keys, attachment-only downloads, owner-scoped access
- **Secrets Management**: Environment variables, no hardcoded credentials

See the [Security Overview](docs/06_SECURITY.md) for details.

## Performance

Document processing baseline (Phase 4, extraction + normalisation only, median of 5 runs; AMD64 Family 25 desktop CPU, Python 3.12, PyMuPDF 1.28; reproduce with `python -m benchmarks.processing_baseline` from `backend/`):

| Document | Chars | Extract | Normalise | Total | Throughput |
|----------|------:|--------:|----------:|------:|-----------:|
| PDF 100 pages, English | 202,500 | 97 ms | 43 ms | 157 ms | 1.3 M chars/s |
| PDF 100 pages, Bangla | 202,500 | 115 ms | 60 ms | 211 ms | 1.0 M chars/s |
| PDF 100 pages, mixed | 202,500 | 160 ms | 62 ms | 257 ms | 0.8 M chars/s |
| DOCX 2,000 paragraphs | 955,950 | 110 ms | 245 ms | 483 ms | 2.0 M chars/s |
| TXT 5 MB | 2,862,450 | 3 ms | 726 ms | 1.1 s | 2.6 M chars/s |
| Markdown 2,000 headings | 537,783 | 11 ms | 121 ms | 177 ms | 3.0 M chars/s |

Chunking baseline (Phase 5, median of 5 runs, same machine; reproduce with `python -m benchmarks.chunking_baseline`):

| Input | Chars | Chunks | Chunking time | Throughput |
|-------|------:|-------:|--------------:|-----------:|
| English, one section | 522,210 | 668 | 5.7 ms | 92 M chars/s |
| Mixed Bangla/English, one section | 2,361,665 | 2,968 | 30 ms | 78 M chars/s |
| Mixed, 500 pages | 780,912 | 1,130 | 10.9 ms | 71 M chars/s |
| DOCX-style, 2,000 lines | 310,693 | 346 | 5.2 ms | 60 M chars/s |
| 1 M characters without whitespace | 1,000,000 | 1,000 | 26.5 ms | 38 M chars/s |

Persisting chunks in batches of 1,000 rows took 74 ms for 1,000 chunks and 676 ms for 10,000, about 9-10x faster than row-by-row inserts (652 ms and 6.7 s). Chunking is a small fraction of end-to-end processing time; extraction dominates.

Chunk quality is measured on a seeded synthetic corpus (`python -m benchmarks.chunking_eval`): on all ten corpus cases every chunk is within the size limit and an exact slice of its source, all non-whitespace text is covered, no chunk starts or ends inside a grapheme cluster, and output is deterministic; prose chunks end on a sentence or paragraph boundary in 100% of cases with a mean size of roughly 65-85% of the limit. This is a structural evaluation of the splitter, not a retrieval-quality measurement; retrieval quality is evaluated in a later phase.

Embedding and search baseline (Phase 6; `python -m benchmarks.embedding_baseline`, same CPU, 6 threads, no GPU, model `multilingual-e5-small`):

| Measure | Result |
|---------|-------:|
| Model load, from cache / first download | 4.6 s / 60.8 s |
| Embedding throughput, 1,000-character chunks, batch 8 (English / Bangla / mixed) | 20.9 / 14.7 / 17.8 chunks/s |
| Query embedding latency (p50 / p95) | 14 / 15 ms |
| Vector search through the real schema, 2,500 of 50,000 vectors (p50) | 6.6 ms |
| Vector search, one user owning 50,000 vectors (p50) | 23.7 ms |
| Search over HTTP in the Docker stack (4 documents, p50 / p95) | 26 / 29 ms |

On CPU, small batches were fastest (batch 4-8; throughput falls as the batch grows), so the default batch size is 8. **GPU throughput was not measured: no GPU was available.** Search is exact by default (no ANN index): per-user exact scans matched an HNSW index on speed with perfect recall in the multi-user case (details and thresholds in DEC-039). Numbers come from one desktop machine and synthetic vectors.

These exclude storage reads, database writes (except the chunk insert figures above) and queueing. Remaining benchmarks are planned for Phase 14:

- Document parsing throughput
- Embedding generation latency
- Search latency (semantic, keyword, hybrid)
- RAG pipeline latency
- End-to-end query latency

## Roadmap

### Near-term (Phases 1-8)
- Complete infrastructure setup
- Implement authentication system
- Build document upload and processing
- Enable semantic and hybrid search
- Deploy RAG with citations

### Mid-term (Phases 9-12)
- Conversation management
- Document versioning
- Evaluation framework
- Frontend application

### Long-term (Phases 13-16)
- Security hardening
- Performance optimization
- CI/CD pipeline
- Production readiness

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, branch strategy, and pull request guidelines.

## License

MIT License. See [LICENSE](LICENSE).

---

**Note**: This project is under active development. Features marked as "Planned" are architecturally designed but not yet implemented. See the roadmap above for the phase breakdown.

*Built as a portfolio project demonstrating production-ready software engineering practices.*
