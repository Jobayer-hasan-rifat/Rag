# Intelligent Document Processing & RAG Platform

> **Status**: 🚧 In Development - Phase 1 complete (Infrastructure). No business features yet.

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
Infrastructure only; there is no user-facing functionality yet.

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
| Start the worker only | `docker compose up -d celery_worker` |

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
│   ├── app/             # api, services, db, workers, storage, observability, schemas
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
| 2 | Authentication | ⏳ Pending |
| 3 | Document Management | ⏳ Pending |
| 4 | Document Processing | ⏳ Pending |
| 5 | Embeddings & Vector Search | ⏳ Pending |
| 6 | Hybrid Retrieval | ⏳ Pending |
| 7 | Reranking | ⏳ Pending |
| 8 | RAG | ⏳ Pending |
| 9 | Conversations | ⏳ Pending |
| 10 | Versioning & Access Control | ⏳ Pending |
| 11 | Evaluation | ⏳ Pending |
| 12 | Frontend | ⏳ Pending |
| 13 | Security Hardening | ⏳ Pending |
| 14 | Performance & Benchmarking | ⏳ Pending |
| 15 | CI/CD & Deployment | ⏳ Pending |
| 16 | Final Audit & Portfolio Prep | ⏳ Pending |

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

Current state (Phase 1): 63 tests passing (unit, API and infrastructure integration),
about 98% line coverage of `app/`. See the [Testing Strategy](docs/11_TESTING_STRATEGY.md).

## Evaluation

This platform includes systematic evaluation of RAG quality:

### Retrieval Metrics
- Recall@K, Precision@K, Hit@K, MRR

### Generation Metrics
- Answer relevance, groundedness, factual consistency

### Citation Metrics
- Citation precision, recall, correctness

*Evaluation framework will be implemented in Phase 11.*

## Security

Security is a first-class concern in this project:

- **Authentication**: JWT with bcrypt password hashing
- **Authorization**: Role-based access control (RBAC)
- **Input Validation**: Pydantic schemas, file validation
- **Document Security**: All uploads treated as untrusted
- **Secrets Management**: Environment variables, no hardcoded credentials

See the [Security Overview](docs/06_SECURITY.md) for details.

## Performance

Performance benchmarks will be established during Phase 14:

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
