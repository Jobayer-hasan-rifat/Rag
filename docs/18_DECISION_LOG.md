# Decision Log

## Overview

This document records major architectural and technical decisions made during the development of the Intelligent Document Processing & RAG Platform.

## Decision Template

```markdown
### DEC-XXX: Decision Title

**Date**: YYYY-MM-DD  
**Status**: Proposed | Accepted | Deprecated | Superseded  
**Context**: What is the issue we're addressing?  
**Options Considered**: What alternatives were evaluated?  
**Chosen Option**: What did we choose?  
**Reason**: Why did we choose this option?  
**Tradeoffs**: What are the pros and cons?  
**Consequences**: What does this decision enable or prevent?
```

---

## Architecture Decisions

### DEC-001: Modular Monolith Architecture

**Date**: 2026-10-07  
**Status**: Accepted

**Context**:  
How should the application be architected? Should we start with microservices or monolith?

**Options Considered**:
1. Microservices from day one
2. Modular monolith
3. Serverless functions

**Chosen Option**:  
Modular monolith with clear domain boundaries.

**Reason**:  
- Simpler initial development and deployment
- Lower operational overhead
- Easier debugging and testing
- Can extract services later if scale demands
- Team size (single developer) doesn't justify microservices complexity

**Tradeoffs**:
- ✅ Simpler deployment
- ✅ Easier local development
- ✅ Simpler debugging
- ✅ Lower infrastructure costs
- ❌ Cannot scale services independently
- ❌ Single point of failure (mitigated by horizontal scaling)

**Consequences**:  
All features are in a single codebase with clear module boundaries. Services can be extracted later if needed.

---

### DEC-002: PostgreSQL + pgvector for Vector Search

**Date**: 2026-10-07  
**Status**: Accepted

**Context**:  
How should we store and search vector embeddings? Should we use a dedicated vector database?

**Options Considered**:
1. PostgreSQL + pgvector
2. Pinecone (managed vector DB)
3. Weaviate (open source vector DB)
4. Milvus (high-performance vector DB)
5. Qdrant (Rust-based vector DB)

**Chosen Option**:  
PostgreSQL with pgvector extension.

**Reason**:  
- Single database for all data (simpler operations)
- pgvector is sufficient for expected scale (thousands to hundreds of thousands of documents)
- No additional infrastructure to manage
- Familiar PostgreSQL ecosystem
- Cost-effective (no separate vector DB costs)

**Tradeoffs**:
- ✅ Simpler architecture (one database)
- ✅ Lower operational overhead
- ✅ No additional costs
- ✅ ACID transactions
- ❌ May not scale to millions of vectors as well as dedicated vector DBs
- ❌ Fewer features than dedicated vector DBs (e.g., hybrid search built-in)

**Consequences**:  
All data (relational and vectors) in PostgreSQL. Can migrate to dedicated vector DB later if scale requires.

**When to Reconsider**:  
- Vector count exceeds 1 million
- Need advanced vector DB features (multi-tenancy, advanced indexing)
- Performance requirements exceed pgvector capabilities

---

### DEC-003: Local Embedding Model vs API

**Date**: 2026-10-07  
**Status**: Accepted

**Context**:  
Should we use a local embedding model or an API service (OpenAI, Cohere)?

**Options Considered**:
1. Local sentence-transformers model
2. OpenAI Embeddings API
3. Cohere Embeddings API
4. Self-hosted inference server

**Chosen Option**:  
Local sentence-transformers model (`all-MiniLM-L6-v2`) as default, with API providers as options.

**Reason**:  
- No API costs for local model
- No external dependency (can work offline)
- Sufficient quality for most use cases
- Privacy (document text doesn't leave the system)
- Flexibility to switch models

**Tradeoffs**:
- ✅ No API costs
- ✅ No external dependency
- ✅ Privacy
- ✅ Configurable
- ❌ Requires CPU/GPU resources
- ❌ Lower quality than OpenAI's best models
- ❌ Model management (downloading, updating)

**Consequences**:  
Default deployment uses local model. Users can configure API providers for higher quality or if they prefer managed service.

---

### DEC-004: Celery for Background Processing

**Date**: 2026-10-07  
**Status**: Accepted

**Context**:  
How should we handle long-running document processing tasks?

**Options Considered**:
1. Celery with Redis
2. RQ (Redis Queue)
3. Dramatiq
4. Background threads
5. AWS Lambda / serverless functions

**Chosen Option**:  
Celery with Redis as message broker.

**Reason**:  
- Battle-tested and mature
- Rich feature set (retries, rate limiting, scheduling)
- Large ecosystem and community
- Excellent monitoring tools (Flower)
- Well-documented
- Works with Redis (already using for cache)

**Tradeoffs**:
- ✅ Mature and stable
- ✅ Rich features
- ✅ Good monitoring
- ❌ Additional infrastructure (Redis)
- ❌ Complexity compared to background threads
- ❌ More complex than RQ

**Consequences**:  
Document processing, embedding generation, and evaluation tasks run asynchronously in Celery workers.

---

### DEC-005: JWT for Authentication

**Date**: 2026-10-07  
**Status**: Accepted

**Context**:  
How should we handle authentication? Session-based or token-based?

**Options Considered**:
1. JWT tokens (access + refresh)
2. Session-based authentication
3. OAuth 2.0 only
4. API keys

**Chosen Option**:  
JWT tokens with short-lived access tokens and long-lived refresh tokens.

**Reason**:  
- Stateless (scales horizontally easily)
- Works well for APIs
- Industry standard
- No server-side session storage required
- Refresh tokens provide good UX/security balance

**Tradeoffs**:
- ✅ Stateless
- ✅ Scales well
- ✅ Mobile-friendly
- ❌ Cannot revoke access tokens before expiry
- ❌ Token management complexity
- ❌ Larger payload than session ID

**Consequences**:  
Access tokens expire in 15 minutes. Refresh tokens (hashed) stored in database for revocation capability.

---

### DEC-006: FastAPI for Web Framework

**Date**: 2026-10-07  
**Status**: Accepted

**Context**:  
Which Python web framework should we use?

**Options Considered**:
1. FastAPI
2. Django
3. Flask
4. Starlette

**Chosen Option**:  
FastAPI.

**Reason**:  
- Native async support
- Automatic OpenAPI documentation
- Type hints and Pydantic integration
- High performance
- Modern Python (3.12+)
- Growing ecosystem

**Tradeoffs**:
- ✅ Async native
- ✅ Auto documentation
- ✅ Type safety
- ✅ High performance
- ❌ Less batteries-included than Django
- ❌ Smaller ecosystem than Django/Flask

**Consequences**:  
Using FastAPI with Pydantic for all API endpoints. Automatic Swagger documentation available.

---

### DEC-007: UUIDs for Primary Keys

**Date**: 2026-10-07  
**Status**: Accepted

**Context**:  
Should we use sequential integers or UUIDs for primary keys?

**Options Considered**:
1. Sequential integers (SERIAL)
2. UUIDs
3. ULIDs

**Chosen Option**:  
UUIDs for all primary keys.

**Reason**:  
- No information leakage about resource count
- Can generate IDs client-side if needed
- No coordination needed for distributed systems
- Harder to enumerate
- Consistent across all tables

**Tradeoffs**:
- ✅ No information leakage
- ✅ Distributed-friendly
- ✅ Security (harder to enumerate)
- ❌ Larger storage (16 bytes vs 4 bytes)
- ❌ Slightly slower inserts
- ❌ Less readable in logs/URLs

**Consequences**:  
All tables use UUID primary keys. URLs look like `/documents/550e8400-e29b-41d4-a716-446655440000`.

---

### DEC-008: Phase 1 Layout and Error Envelope Conventions

**Date**: 2026-10-08
**Status**: Accepted

**Context**:
The Phase 1 brief proposed a slightly different layout from the Phase 0 documents
(`core/config.py`, `api/v1/`, Dockerfiles beside each app) and an error body with
`request_id` inside `error`.

**Chosen Option**:
Keep the documented layout (`app/config.py`, `api/routes`, `observability/`) with `/api/v1` as a router prefix; put
`docker-compose.yml` at the repository root and each Dockerfile in its app directory; keep the documented
`{ "error": {...}, "meta": {request_id, timestamp} }` envelope.

**Reason**:
One consistent contract across docs, API spec and code; a root Compose file makes
`docker compose up` work from a fresh clone.

**Consequences**:
`docker/` is not used; `core/` stays reserved for domain logic.

---

### DEC-009: Real Infrastructure in Integration Tests via Testcontainers

**Date**: 2026-10-08
**Status**: Accepted

**Options Considered**: mocks; tests against the developer's Compose database; Testcontainers.

**Chosen Option**:
Testcontainers start throwaway pgvector PostgreSQL and Redis; CI overrides them with service
containers through `TEST_DATABASE_URL` / `TEST_REDIS_URL`.

**Tradeoffs**:
Tests need Docker and are slower than mocked tests, but they exercise real SQL, pgvector,
migrations and Celery, and do not depend on a developer's machine.

---

### DEC-010: Request ID Trust Policy

**Date**: 2026-10-08
**Status**: Accepted

**Chosen Option**:
Accept a client/proxy `X-Request-ID` only when it matches `[A-Za-z0-9._-]{8,64}`; otherwise generate a UUIDv4.

**Reason**:
Allows end-to-end correlation through a reverse proxy while blocking log injection and
unbounded values. The ID never carries authority.

---

### DEC-011: Reciprocal Rank Fusion for Hybrid Search

**Date**: 2026-10-08
**Status**: Accepted (to be validated in Phase 6/11)

**Context**:
Phase 0 drafts combined cosine similarity and `ts_rank` with fixed weights, but `ts_rank` is
unbounded and the two score scales are not comparable; two documents also described
different normalizations.

**Chosen Option**:
RRF over per-retriever ranks (`k = 60`, optional per-retriever weights).

**Tradeoffs**:
Ignores score magnitude, but needs no normalization and is robust. Parameters will be tuned
against the evaluation dataset, not assumed.

---

### DEC-012: Readiness Scope and Dependency Management

**Date**: 2026-10-08
**Status**: Accepted

**Chosen Option**:
Readiness checks PostgreSQL and Redis only; worker health is a container health check
(`celery inspect ping`). Dependencies are declared as ranges in `pyproject.toml`, with exact pins
generated by `uv` into `requirements*.txt` (the project rules require pinned versions).

**Reason**:
A worker outage should not remove the API from rotation. Pinned lock files give reproducible
builds while `pyproject.toml` stays the single source of intent.

---

## Future Decisions to Make

### To Be Decided

1. **File Storage Backend**: Interface defined in Phase 1 (`StorageProvider`); local filesystem implementation in Phase 3, S3-compatible later
2. **Frontend Framework**: React vs Vue vs Svelte (Phase 12)
3. **CI/CD Platform**: GitHub Actions vs GitLab CI vs Jenkins (Phase 15)
4. **Monitoring Stack**: Prometheus+Grafana vs DataDog vs CloudWatch (Phase 14)
5. **LLM Provider Default**: Which provider to recommend? (Phase 8)

---

*All significant decisions should be recorded here with rationale and consequences.*
