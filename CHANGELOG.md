# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added - Phase 3: Document Management

- Collections (`/api/v1/collections`): create, list, get, rename, delete, and add or remove documents
- Documents (`/api/v1/documents`): multipart upload, filtered/sorted/paginated listing, metadata, rename, streaming download and deletion
- `StorageProvider` extended with streaming save (size and SHA-256), streaming open, size, delete and exists, plus a local filesystem backend using generated storage keys
- File validation: extension, declared type and content signature/structure for PDF, DOCX, TXT and Markdown; filename sanitisation; size limits (declared and streamed); per-user duplicate detection
- Document lifecycle states and an enforced transition map (processing itself arrives in Phase 4)
- Per-user upload rate limiting, a per-user storage quota (403 `QUOTA_EXCEEDED`) and a body-size guard that runs before multipart buffering
- Migration `0003` (collections, documents, document_collections) with same-owner composite foreign keys

### Changed

- `StorageProvider.get()` (bytes) replaced by streaming `open()`; storage keys are opaque instead of path-like (see DEC-018, DEC-023)
- New settings: `STORAGE_BACKEND`, `STORAGE_LOCAL_PATH`, `MAX_UPLOAD_BYTES`, `MAX_STORAGE_BYTES_PER_USER`, `RATE_LIMIT_UPLOAD_ATTEMPTS`; Compose adds an `uploads_data` volume

### Added - Phase 2: Authentication & Authorization

- `POST /api/v1/auth/register`, `/login`, `/refresh`, `/logout` and `GET /api/v1/auth/me`
- User, role and refresh-token tables with Alembic migration `0002` (seeded `user` and `admin` roles)
- bcrypt password hashing (configurable cost, minimum 12 outside development) and a documented password policy
- Short-lived JWT access tokens; opaque, single-use refresh tokens stored as SHA-256 with family-based reuse detection
- Logout revocation: refresh family revoked and access token denylisted in Redis until expiry
- Reusable FastAPI dependencies for the current user, optional user and required roles; ownership helpers for RBAC
- Redis rate limiting for registration, login (per IP and per account) and refresh
- OpenAPI `BearerAuth` security scheme and documented error responses

### Changed

- Phase 0 draft of the user model: `display_name` replaces `username`, `is_superuser` removed (see DEC-014)
- `/auth/refresh` now rotates and returns a new refresh token; `/auth/me` replaces `/users/me` (see DEC-013)
- Login returns a generic 401 for deactivated accounts (see DEC-015)

### Added - Phase 1: Infrastructure

- FastAPI application factory with `/health`, `/health/live`, `/health/ready` and the versioned `/api/v1/health` routes
- Configuration via Pydantic Settings with fail-fast validation (no wildcard CORS, no placeholder secrets outside development)
- Structured JSON logging with request IDs and credential redaction
- Request ID middleware and a consistent error response envelope
- Async SQLAlchemy 2.x engine/session infrastructure and Alembic migrations (the initial migration enables pgvector)
- Redis client, Celery application and a `health_check_task`
- Storage provider interface (abstraction only)
- React + TypeScript + Vite application shell that reads backend readiness
- Docker Compose development environment (PostgreSQL + pgvector, Redis, migrations, backend, worker, frontend) with health checks
- Test infrastructure (pytest, Testcontainers), Ruff, Black, mypy, a Makefile, and a GitHub Actions CI workflow

### Added - 2026-10-07

#### Project Foundation
- Initial project structure and architecture documentation
- Comprehensive engineering documentation system in `docs/`
- Project overview in `README.md`
- Detailed project plan with 17 development phases
- Contributing guidelines
- Code of conduct placeholder
- MIT license

#### Architecture Documentation
- System architecture design with modular monolith pattern
- Database schema design with 10+ entities
- Complete API specification for all endpoints
- Security model
- AI/RAG architecture with provider abstraction
- Document processing pipeline design
- Search and retrieval strategy
- Testing strategy
- Evaluation strategy
- Performance strategy
- Observability design
- Error handling design
- Coding standards
- Git workflow

#### Security Documentation
- Comprehensive security requirements
- Threat model covering file, RAG, authorization, API, and infrastructure threats
- Security controls for authentication, file handling, and input validation

#### Database Design
- Entity-relationship diagram for all models
- User, Role, Collection, Document, DocumentVersion models
- DocumentChunk with vector column design
- Conversation, Message, Citation models
- EvaluationRun, EvaluationResult models
- RefreshToken model for authentication

#### API Specification
- Authentication endpoints (register, login, logout, refresh)
- User management endpoints
- Document CRUD endpoints
- Collection management endpoints
- Search endpoints (semantic, keyword, hybrid)
- Conversation endpoints
- RAG query endpoint
- Evaluation endpoints
- Health check endpoints

#### Development Infrastructure
- `.gitignore` for Python, Node.js, IDE files
- `.env.example` with all required environment variables
- Directory structure design

### Changed
- None (initial release)

### Deprecated
- None

### Removed
- None

### Fixed
- None

### Security
- Established security-first development approach
- Documented threat model before implementation
- Defined security requirements for all phases

---

## Release Phases

### Phase 0: Foundation & Architecture ✅
**Status**: Complete

- Complete documentation system established
- Architecture designed and documented
- Database schema designed
- API specification created
- Security model defined
- Threat model created
- Testing strategy defined
- Project initialized

### Phase 1: Infrastructure
**Status**: Pending

### Phase 2: Authentication
**Status**: Pending

### Phase 3: Document Management
**Status**: Pending

### Phase 4: Document Processing
**Status**: Pending

### Phase 5: Embeddings & Vector Search
**Status**: Pending

### Phase 6: Hybrid Retrieval
**Status**: Pending

### Phase 7: Reranking
**Status**: Pending

### Phase 8: RAG
**Status**: Pending

### Phase 9: Conversations
**Status**: Pending

### Phase 10: Versioning & Access Control
**Status**: Pending

### Phase 11: Evaluation
**Status**: Pending

### Phase 12: Frontend
**Status**: Pending

### Phase 13: Security Hardening
**Status**: Pending

### Phase 14: Performance & Benchmarking
**Status**: Pending

### Phase 15: CI/CD & Deployment
**Status**: Pending

### Phase 16: Final Audit & Portfolio Preparation
**Status**: Pending

---

## Version History

This project uses semantic versioning. Version numbers follow MAJOR.MINOR.PATCH format.

- **MAJOR**: Breaking changes to API or architecture
- **MINOR**: New features, backwards compatible
- **PATCH**: Bug fixes, backwards compatible

### Version Milestones (Planned)

| Version | Milestone | Description |
|---------|-----------|-------------|
| 0.1.0 | Phase 1 Complete | Infrastructure working |
| 0.2.0 | Phase 2 Complete | Authentication working |
| 0.3.0 | Phase 3 Complete | Document management working |
| 0.4.0 | Phase 4 Complete | Document processing working |
| 0.5.0 | Phase 5 Complete | Vector search working |
| 0.6.0 | Phase 6 Complete | Hybrid search working |
| 0.7.0 | Phase 7 Complete | Reranking working |
| 0.8.0 | Phase 8 Complete | RAG working |
| 0.9.0 | Phase 9 Complete | Conversations working |
| 0.10.0 | Phase 10 Complete | Versioning & access control working |
| 0.11.0 | Phase 11 Complete | Evaluation framework working |
| 0.12.0 | Phase 12 Complete | Frontend working |
| 0.13.0 | Phase 13 Complete | Security hardened |
| 0.14.0 | Phase 14 Complete | Performance optimized |
| 0.15.0 | Phase 15 Complete | CI/CD established |
| 1.0.0 | Phase 16 Complete | Production ready |

---

[Unreleased]: https://github.com/username/rag-platform/compare/v0.0.0...HEAD
