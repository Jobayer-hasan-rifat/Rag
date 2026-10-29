# Folio — Intelligent Document Processing & Grounded RAG Platform

Folio is a production-oriented document intelligence and Retrieval-Augmented Generation (RAG) platform. It processes multi-format documents (PDF, scanned images, DOCX, Markdown, plain text), indexes them into a hybrid vector-and-lexical search store, and generates citation-backed answers using local or hosted language models.

Unlike basic "chat with PDF" demos, Folio is engineered with strict grounding verification, asynchronous background workers, document versioning, tenant isolation, and a responsive research workspace.

---

## Key Highlights

- **Multi-Format Ingestion & OCR**: Asynchronous extraction for PDF, DOCX, Markdown, and TXT files. Scanned or image-only PDF pages are automatically transcribed using Tesseract OCR with English and Bengali language support (`eng+ben`).
- **Structure-Aware Chunking**: Documents are split along structural headings, tables, and page boundaries. Chunks maintain character offsets and page references, ensuring citations point to verifiable source locations.
- **Multilingual Embeddings & Hybrid Search**: Vector embeddings (`intfloat/multilingual-e5-small`) stored in PostgreSQL via `pgvector` are combined with PostgreSQL full-text search using **Reciprocal Rank Fusion (RRF)**. Quoted phrases, exact error codes, and conceptual semantics are captured in both English and Bengali.
- **Optional Cross-Encoder Reranking**: High-precision multi-lingual cross-encoder (`BAAI/bge-reranker-v2-m3`) re-scores retrieval candidates for maximum relevance.
- **Grounded RAG Generation**: Language models (Ollama by default, or any OpenAI-compatible API) generate answers restricted to bounded retrieved passages. Sentences cite `[S1]` markers mapped to database records. Fabricated citations, ungrounded numbers, or unsupported quotes are stripped deterministically.
- **Persistent Conversations**: Multi-turn chat sessions with database-enforced user isolation, turn-level idempotency (`Idempotency-Key`), and crash recovery. Conversation history is framed as untrusted context so it cannot be hallucinated into factual evidence.
- **Document Versioning**: Stable document identities allow atomic re-uploads and version upgrades without taking search offline. Historical conversation citations preserve immutable pointers to the exact version from which they were derived.
- **Security Hardened**: Token rotation with reuse detection, rate-limiting with trusted reverse-proxy CIDR filtering (`X-Forwarded-For`), request body caps, strict Content Security Policy, and sanitized rendered outputs.
- **Modern Responsive Workspace**: React 18 and TypeScript interface featuring light/dark themes, accessible keyboard navigation, a collapsible thread view, and a docked, resizable source passage reader.

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                 React + TypeScript Workspace                │
└──────────────────────────────┬──────────────────────────────┘
                               │ HTTPS / JSON API
┌──────────────────────────────▼──────────────────────────────┐
│                    FastAPI Backend Service                  │
│  ┌───────────────┬────────────────┬──────────────────────┐  │
│  │ Auth & Users  │ Documents/Vers │ Search & RAG Service │  │
│  └───────────────┴────────────────┴──────────────────────┘  │
└──────────────┬───────────────────────────────┬──────────────┘
               │                               │
┌──────────────▼──────────────┐ ┌──────────────▼──────────────┐
│   PostgreSQL 16 + pgvector  │ │            Redis            │
│ - Relational Data & Users   │ │ - Task Queue Broker         │
│ - Chunks & Embeddings       │ │ - Rate Limit Counters       │
│ - Full-Text GIN Index       │ │ - Revoked Token Cache       │
│ - Conversations & Citations │ └──────────────┬──────────────┘
└─────────────────────────────┘                │
                                ┌──────────────▼──────────────┐
                                │        Celery Workers       │
                                │ - Text Extraction & OCR     │
                                │ - Chunking & Vectorization  │
                                │ - Maintenance Sweeps        │
                                └──────────────┬──────────────┘
                                               │
                                ┌──────────────▼──────────────┐
                                │     LLM Inference Server    │
                                │  (Local Ollama or API)      │
                                └─────────────────────────────┘
```

---

## Technology Stack

| Layer | Technology | Purpose |
|---|---|---|
| **Backend API** | Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2.x | Async web service, API contracts, database ORM |
| **Database** | PostgreSQL 16 + pgvector | Relational schema, GIN text search, vector embeddings |
| **Task Queue & Cache** | Redis 7, Celery | Async ingestion pipeline, distributed tasks, rate limiting |
| **Embeddings & ML** | `multilingual-e5-small`, `bge-reranker-v2-m3` | 384-d semantic embeddings, cross-encoder reranking |
| **Document Processing** | PyMuPDF, python-docx, Tesseract OCR | Native PDF parsing, DOCX extraction, scanned OCR |
| **Frontend** | React 18, TypeScript, Vite, CSS Design Tokens | Responsive SPA, source panel docking, theme switching |
| **Containerization** | Docker, Docker Compose | Multi-container local & staging orchestration |
| **Testing & CI** | pytest, pytest-asyncio, Testcontainers, Vitest | Unit, API integration, E2E evaluation, CI workflows |

---

## Quick Start (Docker Compose)

The entire stack (API, workers, PostgreSQL with pgvector, Redis, and Frontend) runs with Docker Compose.

### 1. Clone & Configure

```bash
git clone https://github.com/Jobayer-hasan-rifat/Rag.git
cd Rag

# Copy the environment template
cp .env.example .env
```

Review `.env` and adjust credentials or flags as needed.

### 2. Launch Services

```bash
docker compose up -d --build
```

Database migrations run automatically during startup. Check status until all containers report healthy:

```bash
docker compose ps
```

| Component | URL | Notes |
|---|---|---|
| **Web Interface** | `http://localhost:5173` | React frontend workspace |
| **API Server** | `http://localhost:8000` | FastAPI application |
| **Interactive Docs** | `http://localhost:8000/docs` | Swagger UI (enabled in development) |
| **Readiness Health** | `http://localhost:8000/health/ready` | Verifies DB and Redis connectivity |

### 3. Enabling RAG Answers

By default, answer generation is toggled off (`RAG_ENABLED=false`) so the platform can run in pure search mode without requiring an active LLM.

To enable generative answers:
1. Start an [Ollama](https://ollama.ai) instance locally or configure an OpenAI-compatible endpoint:
   ```bash
   ollama pull qwen3.5:9b
   ```
2. In `.env`, set:
   ```env
   RAG_ENABLED=true
   LLM_PROVIDER=ollama
   LLM_ENDPOINT=http://host.docker.internal:11434
   LLM_MODEL=qwen3.5:9b
   ```
3. Restart the backend service: `docker compose restart backend celery_worker`.

---

## API Usage Examples

### 1. Authenticate

```bash
# Register
curl -s -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "user@example.com", "password": "SecurePassword123!"}'

# Login to obtain an access token
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "user@example.com", "password": "SecurePassword123!"}' | jq -r .access_token)
```

### 2. Upload a Document

```bash
curl -s -X POST http://localhost:8000/api/v1/documents \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@annual_report.pdf"
```

The server returns immediately with document metadata and status `pending`. Celery workers parse, chunk, and embed the document until status reaches `ready`.

### 3. Search (Semantic or Hybrid)

```bash
# Hybrid Search (dense vectors + full-text RRF fusion)
curl -s -X POST http://localhost:8000/api/v1/search/hybrid \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "quarterly revenue growth", "top_k": 5, "rerank": true}'
```

### 4. Ask a Question with Grounded Citations

```bash
# Create a conversation
CONV_ID=$(curl -s -X POST http://localhost:8000/api/v1/conversations \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title": "Financial Analysis"}' | jq -r .id)

# Ask a question inside the conversation
curl -s -X POST "http://localhost:8000/api/v1/conversations/$CONV_ID/messages" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $(uuidgen)" \
  -d '{"content": "What was the total net revenue reported?"}'
```

---

## Local Development (Without Docker)

### Backend Setup

```bash
cd backend

# Create virtual environment and install dependencies
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

# Start backing PostgreSQL and Redis containers
docker compose up -d postgres redis

# Run database migrations
alembic upgrade head

# Run backend API with live reload
uvicorn app.main:get_app --factory --reload --port 8000

# Run worker in a separate terminal
celery -A app.workers.celery_app:celery_app worker -l INFO
```

### Frontend Setup

```bash
cd frontend
npm install
npm run dev  # Starts Vite dev server at http://localhost:5173
```

---

## Testing & Quality Assurance

### Backend Test Suite

```bash
cd backend

# Run fast unit and API tests (no external services needed)
python -m pytest -m "not integration"

# Run integration tests against real PostgreSQL/Redis containers
python -m pytest -m integration

# Run full test suite with coverage
python -m pytest --cov=app

# Static analysis and linting
python -m ruff check .
python -m black --check .
python -m mypy
```

### Frontend Test Suite

```bash
cd frontend

# Run unit tests
npm test

# Type checking and production build
npm run typecheck
npm run build

# Accessibility contrast audit
npm run check:contrast
```

### RAG Evaluation Framework

Folio includes a dedicated reproducible evaluation framework located in [`backend/evaluation/`](backend/evaluation/):
- **Deterministic Evaluation**: Validates tenant isolation, version scoping, citation consistency, and abstention policies without requiring a live model in CI.
  ```bash
  python -m evaluation.suite --profile deterministic --baseline evaluation/baselines/deterministic.json
  ```
- **Live Model Evaluation**: Measures citation precision, fact recall, candidate recall at `k`, and MRR across English, Bengali, and cross-lingual test corpora.

---

## Project Structure

```
.
├── backend/
│   ├── alembic/              # Database migration versions
│   ├── app/
│   │   ├── api/              # FastAPI route handlers & middleware
│   │   ├── core/             # Document chunking, hashing & extraction models
│   │   ├── db/               # Repositories & SQLAlchemy models
│   │   ├── embeddings/       # Embedding providers & download utilities
│   │   ├── parsers/          # PDF, DOCX, TXT, Markdown, and Tesseract OCR
│   │   ├── rag/              # Prompts, context assembly & grounding validation
│   │   ├── services/         # Business logic layer
│   │   └── workers/          # Celery background tasks & beat schedules
│   ├── evaluation/           # Evaluation suites, baselines, and benchmarks
│   └── tests/                # Unit, API, and integration test suites
├── frontend/
│   ├── src/
│   │   ├── components/       # UI components (UploadZone, SourcePanel, etc.)
│   │   ├── pages/            # Workspace, Library, Search, Auth views
│   │   ├── services/         # Typed API clients & session management
│   │   └── styles/           # CSS design tokens & themes
│   └── index.html
├── docs/                     # Detailed architectural specifications & guides
├── docker-compose.yml        # Development orchestration
└── .github/workflows/        # Continuous integration workflows
```

---

## Detailed Documentation

Comprehensive architectural design records and threat models are maintained in [`docs/`](docs/):

- [Architecture Design](docs/02_ARCHITECTURE.md)
- [API Specification](docs/05_API_SPECIFICATION.md)
- [Security & Threat Model](docs/06_SECURITY.md)
- [Database Schema](docs/04_DATABASE_SCHEMA.md)
- [Product Behavior Guide](docs/PRODUCT_BEHAVIOR_GUIDE.md)
- [Testing Strategy](docs/11_TESTING_STRATEGY.md)
- [Decision Log](docs/18_DECISION_LOG.md)

---

## License

This project is open-source under the [MIT License](LICENSE).
