# Intelligent Document Processing & RAG Platform

> **Status**: 🚧 In Development - Phase 0 (Foundation & Architecture)

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
*None yet - currently in Phase 0 (Architecture & Foundation)*

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

> ⚠️ **Note**: Setup instructions will be available after Phase 1 (Infrastructure) is complete.

```bash
# Clone repository
git clone https://github.com/Jobayer-hasan-rifat/Rag.git
cd rag-platform

# Copy environment variables
cp .env.example .env

# Start services
docker-compose up -d

# Run database migrations
docker-compose exec backend alembic upgrade head

# Access application
open http://localhost:3000
```

## Project Structure

```
/
├── backend/           # Python FastAPI application
│   ├── app/          # Application code
│   ├── tests/        # Test suite
│   └── alembic/      # Database migrations
├── frontend/          # React TypeScript application
├── docs/              # Engineering documentation
├── docker/            # Docker configurations
└── scripts/           # Development scripts
```

## Development Status

| Phase | Description | Status |
|-------|-------------|--------|
| 0 | Foundation & Architecture | 🚧 **Current** |
| 1 | Infrastructure | ⏳ Pending |
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

> Testing infrastructure will be established in Phase 1. No tests or results exist yet; see the [Testing Strategy](docs/11_TESTING_STRATEGY.md).

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=app --cov-report=html

# Run specific test types
pytest tests/unit/          # Unit tests
pytest tests/integration/   # Integration tests
pytest tests/e2e/           # End-to-end tests
```

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
