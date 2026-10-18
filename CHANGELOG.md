# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed - Phase 13.1: Interface Redesign

- New visual identity (name, mark, warm-white and slate palette with one blue accent, serif reading type, system fonts with a Bengali fallback) defined as tokens, with light, dark and follow-the-device themes applied before first paint
- Shell: a slim top bar replaces the sidebar on large screens; a bottom tab bar and compact top bar on phones; an account menu with theme and sign out; one *Add documents* dialog available everywhere that keeps uploading when closed
- Workspace: collapsible conversation list, question-and-answer entries with numbered source markers, and a source panel docked beside the thread that can be resized and closed (a drawer below 1180 px, a bottom sheet on phones); composer that grows with its text, scopes one question to chosen documents and sends with Enter only on keyboard devices; a question that never reached the server can be sent again after a reload
- Documents: compact table with row menus (details, ask about it, download, delete) and stacked rows on phones; clearer version history that states which version search uses, which are searchable and why an older citation says "earlier version"
- Search: one prominent field with options summarised on one line; results offer view source, open document and ask about that document (a new conversation limited to it)
- Home: a three-step start for an empty library, and a continue-working view otherwise (no decorative statistics)
- Sign-in and registration with a password show/hide control; settings gain an appearance choice
- Accessibility: contrast-checked tokens (`npm run check:contrast`, also run by `npm test`), keyboard-operable menus and resizer, named icon controls, one `h1` per page, announced upload and processing changes, 44 px touch targets, section titles and focus moved on navigation
- `frontend/DESIGN.md` describing the design and its references

### Fixed

- A search opened from a link (`/search?q=...`) stayed on "Searching" under React StrictMode in development because the aborted first request was never repeated

### Added - Phase 13: Frontend Integration

- React application over the existing API: sign-in and registration, dashboard, documents (drag-and-drop upload with progress and duplicate handling, filters, sorting, pagination), document details with version history (upload, activate, retry, delete, download), semantic and hybrid search with optional rerank and a source panel, and persistent conversations with citations
- Authentication: access token in memory, refresh token in `sessionStorage`, single-flight refresh with one retry, protected routes with no flash of protected content
- Conversations: `Idempotency-Key` on every question with the key kept for a retry, reconciliation after a reload, polling while a question is pending, explicit handling of busy, in-progress, key-reuse, rate-limit and provider failures, no streaming
- Citations name their version; superseded citations say so and show only their own version's text; deleted sources show no text
- Safe Markdown renderer (no raw HTML, restricted link schemes), typed API client with centralised error mapping, design tokens in plain CSS, native dialogs, toasts, skeletons, reduced-motion support
- Frontend tests (`npm test`, vitest) and `frontend/README.md`

### Fixed

- CORS: the API now allows the `Idempotency-Key` request header and exposes `Retry-After`; without the first, a browser could not send a conversation message at all (the preflight was rejected)

### Added - Phase 12: End-to-End Evaluation

- `python -m evaluation.suite` (profiles `deterministic` and `models`): retrieval arms with bootstrap and Wilson intervals, candidate recall, reranking quality against cost (cold and warm latency, memory), filter and candidate checks, 35 pipeline scenarios, the answer questions and a citation audit; JSON reports with a stable schema and run metadata
- Scenario dataset (`evaluation/scenarios/`): document versions (replacement, failed replacement, restore, re-processing, deletion), stored citations after each, conversation history (follow-ups, planted instructions, wrong earlier answers, deleted sources), abstention, injected documents, provider faults, degraded retrieval and tenant isolation, with `hard` (pipeline guarantees) and `behaviour` expectations
- Dataset cards computed from the data (counts, categories, hashes, limitations), `python -m evaluation.cards`
- `python -m evaluation.compare_runs`: hard gates that a quality score cannot override, quality and performance warnings with tolerances derived from sample noise, refusal to compare incompatible runs; committed deterministic baseline and a CI step that runs it
- `python -m evaluation.citation_audit`: automatic review flags (missing citation, unsupplied label, number, quotation or identifier absent from the cited passage, low overlap) and a seeded human-review template with a rubric; no review results are recorded
- Evaluation tests (`tests/integration/test_evaluation_scenarios.py`, `tests/unit/test_evaluation_framework.py`), including a check that the deterministic tooling imports no ML package

### Changed

- The earlier evaluation loader creates a document family for each evaluation document (it predated versions) and accepts an id namespace; recorded results are unchanged and reproduce exactly with `--legacy-ids`

### Fixed

- A typing error in a Phase 11 test helper that the project's full type check reports

### Added - Phase 11: Document Versions

- `document_families` and per-document versions (migration `0009`, reversible while every document has one version): stable document id, `version_number`, `is_active` and `is_current` flags with partial unique indexes, an owner foreign key, and a trigger that only lets a ready version become active. Existing documents are backfilled as version 1 with the same id
- `POST /api/v1/documents/{id}/versions`, `GET .../versions`, `GET .../versions/{version_id}`, `POST .../retry`, `POST .../activate` and `DELETE .../versions/{version_id}`; owner-only, rate limited, validated; documents report `version_id`, `version_number`, `version_count`, `active_version_id` and `latest_version`
- Atomic activation under the family lock when a version becomes ready and is newer than the active one; a failed replacement or a late older job never changes the active version; deleting a document removes every version and file
- Retrieval (dense, keyword, hydration) returns only ready, active versions; filters name documents; results and citations report `version_id` and `version_number`
- Conversation citations resolve against the exact cited version (`superseded` flag; no cross-version text); deleting a version or document scrubs stored references
- `MAX_VERSIONS_PER_DOCUMENT` setting; structured logs for version upload, activation, retry and deletion
- `python benchmarks/versioning_baseline.py` measures listing, version listing and retrieval eligibility queries

### Changed

- `document_collections` references the document (family), not a version; `DUPLICATE_DOCUMENT` reports the document id; the first search/answer/citation fields gain `version_id` and `version_number` (additive)

### Added - Phase 10: Persistent Conversations

- `conversations` and `messages` tables (migration `0008`, reversible): ownership enforced by a composite foreign key, per-conversation sequence numbers, one answer per question, one in-flight question per conversation, idempotency keys unique per conversation, citations stored as references only, cascade on conversation and account deletion
- `/api/v1/conversations`: create, list (paginated, newest activity first), read with a bounded message page, rename, delete, and `POST /{id}/messages`; every endpoint is owner-scoped (a foreign conversation is the same 404 as a missing one), bodies are capped at 128 KiB, message generation has its own rate limit
- Conversation service on top of the existing RAG service: question stored first, no transaction or connection held while retrieving or generating, answer stored in a second short transaction; failures keep the question as `failed` and store no answer; stale in-flight questions are marked interrupted; `Idempotency-Key` replay and retry
- Bounded, escaped, delimited conversation history under prompt version `answer_v2`; short follow-ups are retrieved together with the previous question; filters are per question; citations are validated per answer
- Deterministic titles (no model call), NFC-normalised and stripped of control and invisible format characters
- Deleting a document removes its name and headings from stored citation references
- `CONVERSATION_*` and `RATE_LIMIT_CONVERSATION_*` settings in `.env.example` and Compose; a validated stale-turn window
- `python -m benchmarks.conversation_baseline` measures list and history latency, database statements per turn and the effect of history

### Changed

- Conversations are hard-deleted (DEC-063); the response builders for answers moved into a shared presenter used by both `/rag/answer` and conversations; `/rag/answer` behaviour is unchanged

### Added - Phase 9: Retrieval-Augmented Generation

- Generation-provider layer (`app/llm/`): an async `LLMProvider` interface with a local Ollama provider and an OpenAI-compatible provider over `httpx` (no vendor SDK); configurable model, temperature, output and context limits, timeout and concurrency; keys only from the environment, never over plain http to a remote host, no environment proxy, no redirects, provider bodies never logged or returned
- `POST /api/v1/rag/answer`: authenticated, own rate limit, 128 KiB body cap, owner-scoped hybrid or semantic retrieval with optional Phase 8 reranking, bounded context, validated answer with citations built from database records, explicit insufficient-evidence and failure responses, retrieval and reranking degradation reported separately; off by default (`RAG_ENABLED=false`)
- Context builder (`app/rag/context.py`): deterministic selection, near-duplicate removal, per-source and total token budgets measured on the escaped prompt text, stable labels `S1..Sn`; versioned prompt file (`app/rag/prompts/answer_v1.txt`); XML-escaped sources with neutralised marker-like text
- Local output validation (`app/rag/grounding.py`): strict JSON parsing, unknown labels removed, uncited sentences, unsupported quotations and unsupported numbers or identifiers dropped, answer-level citation fallback reported as such
- `LLM_*`, `RAG_*` and `RATE_LIMIT_RAG_ATTEMPTS` settings in `.env.example` and Compose (the container reaches a host Ollama through `host.docker.internal`)
- Answer-generation evaluation (`python -m evaluation.rag_eval`) with a 48-question dataset (`evaluation/rag/`) including unanswerable, contradictory and adversarial cases, deterministic metrics and stored results for three local models

### Changed

- `HybridSearchService.search` accepts a server-set candidate limit (used by reranking and RAG); the semantic and hybrid endpoints are unchanged

### Added - Phase 8: Optional Reranking

- Cross-encoder reranking layer (`app/reranking/`): a `Reranker` interface independent of the embedding provider, a transformers-based multilingual cross-encoder (pinned revision, safetensors only, no remote code, architecture and single-output validation), pure ordering logic with deterministic ties, a registry that loads one model per process lazily, and `python -m app.reranking.download`
- `rerank` and `rerank_candidates` options on `POST /api/v1/search/hybrid`: opt-in, candidates only from owner-scoped hybrid retrieval, bounded candidate count and `top_k`, reranked position, raw score, original retrieval rank and truncation flag per result, and an explicit fallback (`rerank.applied: false`, `degraded: ["rerank"]`) when reranking cannot run
- Inference in a worker thread behind a concurrency gate and deadline, after releasing the database connection; optional background preload at start-up
- `RERANK_*` settings in `.env.example` and Compose; the embedding worker fetches the model when `RERANK_ENABLED=true`
- Reranking ablation (`python -m evaluation.rerank_eval`) with a separate held-out dataset; evaluation ids are now deterministic so results are reproducible

### Changed

- Default behaviour of `/search/hybrid` and `/search/semantic` is unchanged; reranking is disabled by default
- Phase 7 evaluation figures were re-run with fixed chunk ids (ties in fusion were broken by random ids before); hybrid MRR@10 is 0.931 rather than 0.919

### Added - Phase 7: Hybrid Retrieval

- Lexical (full-text) retrieval over `document_chunks`: a generated `tsvector` (`simple` configuration, no stemming, safe for Bengali) and a composite GIN index `(user_id, search_vector)` with a trigger-filled, foreign-key-protected owner column (migration `0007`, needs the `btree_gin` extension)
- Inverse-document-frequency term weighting computed inside the caller's own corpus, quoted phrases, function-word filtering (English and Bengali), bounded term and phrase counts
- Reciprocal Rank Fusion (`app/retrieval/fusion.py`): deterministic, rank-based, configurable `k` and weights
- `POST /api/v1/search/hybrid`: owner-scoped, rate limited, 128 KiB body cap, per-result dense and lexical ranks, diagnostics, and graceful degradation when one retriever is unavailable
- Dense search split into candidate generation and hydration (ownership and `ready` re-checked); `POST /api/v1/search/semantic` is unchanged
- `HYBRID_*` and `SPARSE_*` settings in `.env.example` and Compose
- Evaluation: dense, sparse and hybrid ablation (`python -m evaluation.hybrid_eval`) over a lexical companion dataset (identifiers, phrases, names, inflection, paraphrase, unanswerable) and the Phase 6 dataset; hybrid latency benchmark through the real schema (`python -m benchmarks.hybrid_search_baseline`)

### Added - Phase 6: Embeddings & Semantic Search

- Embedding provider interface (`app/embeddings/`) with a local sentence-transformers provider: pinned revision required, safetensors only, no remote code, CPU or GPU, validated vectors; providers register through a registry
- Default model `intfloat/multilingual-e5-small` (384 dimensions), chosen by measuring four candidates on a Bangla/English/mixed dataset (DEC-037); `python -m app.embeddings.download` fetches it
- `embedding_configs` and `chunk_embeddings` tables (migration `0006`): pgvector cosine vectors tied to a chunk of the same document and to its owner by composite foreign keys, one vector per chunk and configuration, with embedding progress columns on documents
- Embedding stage: dedicated `embedding` queue and `embedding_worker` service, batched idempotent time-bounded tasks, retries, heartbeat-based crash recovery, `reindex_embeddings` maintenance task, new failure reasons `model_unavailable`, `embedding_failed`, `invalid_embedding`
- `POST /api/v1/search/semantic`: authenticated, per-user rate limited, owner-scoped, bounded `top_k` and query length, document/collection filters, 128 KiB body cap, scores as cosine similarity
- Document responses gain `embedded_chunk_count`, `embedding_completed_at` and `searchable`
- Retrieval evaluation framework (`backend/evaluation/`: dataset, Recall@k/MRR/nDCG@k/AUROC, model comparison and end-to-end runners) and benchmarks for embedding throughput and vector search
- `EMBEDDING_*` and `SEARCH_*` settings in `.env.example` and Compose; CPU-only PyTorch in the image, models in a shared `model_cache` volume

### Changed

- `ready` now means every chunk has a vector (searchable); documents leave `chunked` through `embedding`. Phase 5 documents left in `chunked` are embedded automatically
- An embedding failure keeps the document's chunks, and a manual retry resumes at `chunked`
- Re-chunking a searchable document drops its vectors and queues it for embedding again
- The default embedding model changed from the English-only `all-MiniLM-L6-v2` in the original design to a multilingual one

### Added - Phase 5: Intelligent Chunking

- Structure-aware, deterministic chunker (`app/core/chunking/`): chunks never cross a page or heading boundary, are exact slices of their section, keep page number, heading and heading path, and respect a configurable size limit; linear-time on adversarial input
- Paragraph, table and fenced-code awareness; sentence splitting that understands the Bengali danda; conjunct- and ZWJ/ZWNJ-safe hard cuts; overlap only inside oversized blocks
- `document_chunks` table and `chunk_count` / `chunking_version` on documents (migration `0005`), with idempotent replace-all persistence in batches
- New `chunked` status ("chunks generated, ready for embedding"); `ready` now means embedded and searchable
- `GET /api/v1/documents/{id}/chunks` (owner-scoped, paginated) and chunk fields on document responses
- `rechunk_document` maintenance task to regenerate chunks from stored sections without re-extraction
- `too_many_chunks` and `chunking_failed` failure reasons, `CHUNKING_*` settings, chunking quality evaluation (`benchmarks/chunking_eval.py`) and baseline (`benchmarks/chunking_baseline.py`)

### Changed

- The processing pipeline now ends in `chunked` instead of `ready`; documents that Phase 4 left in `ready` are returned to `pending` by the migration and reprocessed
- A failed or re-queued document no longer keeps chunks or counts from an earlier run

### Added - Phase 4: Document Processing

- Asynchronous processing pipeline: upload queues a Celery task; a worker extracts, normalises and stores each document's text and marks it `ready` (or `failed`)
- Extractors behind a common interface for PDF (PyMuPDF, page-aware), DOCX (python-docx, headings and tables), Markdown (heading-aware) and plain text
- Conservative Unicode-safe normalisation that preserves Bangla, ZWJ/ZWNJ, case and punctuation; script profile (Bengali/Latin/mixed) stored per document
- `document_sections` table and processing columns on `documents` (migration `0004`): page count, character count, timestamps, attempts, failure reason, metadata
- `POST /api/v1/documents/{id}/retry`; document responses now include processing state, counts and `failure_reason`
- Classified failures with safe messages, bounded retries with backoff, atomic claiming, and a Celery beat recovery sweep for stuck or lost work
- Configurable limits (pages, text size, DOCX archive size, time budget, worker recycling), a hardened worker container (read-only filesystem, dropped capabilities, memory and process caps) and a `celery_beat` service
- Benchmark script `benchmarks/processing_baseline.py`

### Changed

- Lifecycle: `parsing` now means "being processed"; added `parsing -> ready` and `parsing -> pending` transitions (see DEC-025)
- Worker consumes the `processing` queue in addition to the default queue
- API task publishing no longer subscribes to Celery result channels

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
| 0.5.0 | Phase 5 Complete | Document chunking working (vector search follows) |
| 0.6.0 | Phase 6 Complete | Embeddings and semantic search working (hybrid search follows) |
| 0.7.0 | Phase 7 Complete | Hybrid retrieval working (reranking follows) |
| 0.8.0 | Phase 8 Complete | Optional reranking working (RAG follows) |
| 0.9.0 | Phase 9 Complete | Grounded RAG answers working (conversations follow) |
| 0.10.0 | Phase 10 Complete | Versioning & access control working |
| 0.11.0 | Phase 11 Complete | Evaluation framework working |
| 0.12.0 | Phase 12 Complete | Frontend working |
| 0.13.0 | Phase 13 Complete | Security hardened |
| 0.14.0 | Phase 14 Complete | Performance optimized |
| 0.15.0 | Phase 15 Complete | CI/CD established |
| 1.0.0 | Phase 16 Complete | Production ready |

---

[Unreleased]: https://github.com/username/rag-platform/compare/v0.0.0...HEAD
