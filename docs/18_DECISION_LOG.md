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

### DEC-013: Authentication Token Design

**Date**: 2026-10-08
**Status**: Accepted (refines DEC-005)

**Context**:
DEC-005 chose JWT access and refresh tokens but left signing, rotation and revocation open, and
accepted that access tokens could not be revoked.

**Chosen Option**:
- Access token: short-lived HS256 JWT (PyJWT) carrying identity only (`sub`, `jti`, `type`, `iss`, `aud`, `iat`, `exp`).
- Refresh token: opaque random value, stored as SHA-256, single use, rotated within a *family*; reuse revokes the family.
- Role and active status are loaded from the database on each request, not trusted from the token.
- Logout revokes the family and denylists the access token `jti` in Redis until expiry.
- `/auth/refresh` returns a new access *and* refresh token (the Phase 0 draft returned only an access token).

**Reason**:
Rotation with reuse detection limits the damage of a stolen refresh token. DB-authoritative roles make deactivation and
demotion immediate, at the cost of one indexed query per request. HS256 is sufficient while one service both
issues and verifies tokens.

**Tradeoffs**:
Not purely stateless (a DB read and a Redis read per authenticated request); a client that loses a refresh response
and retries will be logged out. Moving to asymmetric signing is straightforward if other services must verify tokens.

---

### DEC-014: User Model Changes from the Phase 0 Draft

**Date**: 2026-10-08
**Status**: Accepted

**Chosen Option**:
`display_name` (not unique) replaces the unique `username`; `is_superuser` is dropped in favour of the `role`
relationship; `password_hash` replaces `hashed_password`; emails carry a lowercase CHECK constraint;
`GET /auth/me` replaces `GET /users/me`.

**Reason**:
A unique handle adds another enumeration surface and a second identity to keep consistent; the email is
the login identifier. A single source of privilege (role) avoids two flags drifting apart.

---

### DEC-015: Account Enumeration and Account-State Policy

**Date**: 2026-10-08
**Status**: Accepted

**Chosen Option**:
Login returns one generic 401 for unknown, wrong-password, deactivated and deleted accounts (the Phase 0 draft returned 400 for deactivated
accounts, which confirmed valid credentials); unknown accounts still incur a bcrypt comparison.
Registration returns a neutral 409 for existing emails.

**Tradeoffs**:
Deactivated users get no explanatory message (support must tell them). Registration existence is still observable;
accepted until email verification exists.

---

### DEC-016: Authentication Rate Limiting

**Date**: 2026-10-08
**Status**: Accepted

**Chosen Option**:
Fixed-window counters in Redis, per client IP for register/login/refresh and per account for login at 5x the IP
budget. Fails open if Redis is down; cannot be disabled in staging or production. A library such as `slowapi` was not
adopted because it would add a dependency for three call sites and does not give per-account budgets.

**Tradeoffs**:
Fixed windows allow a burst across a window boundary. The IP is the direct peer; proxy deployments need trusted-proxy configuration (to be configured at deployment).

---

### DEC-017: Keep bcrypt for Password Hashing

**Date**: 2026-10-08
**Status**: Accepted

**Context**:
The Phase 2 brief allowed Argon2id or another modern algorithm "supported by the project's security requirements".

**Chosen Option**:
bcrypt (cost >= 12 in staging/production), the algorithm named in the project's security rules. Inputs over 72 bytes are
rejected.

**Tradeoffs**:
Argon2id is more resistant to GPU attacks and has no length limit; migrating later is possible by storing the
algorithm in the hash prefix and re-hashing on login.

---

### DEC-018: Opaque Storage Keys

**Date**: 2026-10-08
**Status**: Accepted

**Context**:
The Phase 0 sketch stored files at `/uploads/{user_id}/{document_id}/{version}/{filename}`, which embeds user input and ties storage layout to ownership and versions.

**Chosen Option**:
Keys are generated server-side (`documents/{2 hex}/{32 hex}`, 128 random bits). The local backend accepts only that exact pattern. Original filenames, owners and versions exist only in PostgreSQL.

**Tradeoffs**:
Storage can no longer be browsed by user, so reconciliation must go through the database. In exchange, path traversal through names is structurally impossible and moving to S3 needs no layout changes.

---

### DEC-019: Upload Pipeline Ordering

**Date**: 2026-10-08
**Status**: Accepted

**Chosen Option**:
Authentication and rate limiting run before the body is parsed (the route parses the form itself, after dependencies); a middleware caps the body before it is spooled; validation and hashing happen first, then a duplicate check, then the streamed save, then the database insert with compensating deletion of the stored file on failure. Identical content per user is a 409 (`UNIQUE (user_id, checksum_sha256)`).

**Reason**:
FastAPI parses declared form parameters before running dependencies, so the usual `UploadFile` parameter would buffer arbitrarily large bodies for unauthenticated or throttled callers.

**Tradeoffs**:
The OpenAPI body schema is supplied manually, and the file is read twice (validate, then store) from a local temporary file. Storage and database cannot share a transaction, so a crash between the two steps can orphan an object; this is logged, and a reconciliation job is planned.

---

### DEC-020: Ownership, 404 Semantics and Administrator Access

**Date**: 2026-10-08
**Status**: Accepted

**Chosen Option**:
Missing and not-owned resources return the same 404. The documented RBAC policy (FR-1.4) lets administrators access any document or collection by ID (read, download, rename, delete); listing and uploading always act on the caller's own data. Database composite foreign keys guarantee that document-collection links never cross owners.

**Tradeoffs**:
Any administrator can read any user's files by ID. This follows the Phase 0 requirements; if stricter privacy is wanted, restricting `can_access_owned_resource` to owners is a one-line change plus an audited break-glass endpoint.

---

### DEC-021: Document Lifecycle

**Date**: 2026-10-08
**Status**: Accepted

**Chosen Option**:
Keep the Phase 0 statuses (`pending, parsing, chunking, embedding, indexing, ready, failed`) with a transition map: forward one stage, any in-progress stage may fail, and `ready`/`failed` may return to `pending`. There is no `deleted` status because deletion is a hard delete (FR-2.4). `error_message` is only valid when `failed`.

---

### DEC-022: Deletion and Consistency Policy

**Date**: 2026-10-08
**Status**: Accepted

**Chosen Option**:
Delete the stored object first, then the record. Storage failure keeps the record (503, retryable). A missing object is logged at ERROR and deletion proceeds. Download of a record whose object is missing returns 500 `STORAGE_INCONSISTENCY`.

**Reason**:
A user is never told a document is gone while data remains; inconsistencies are loud for operators and generic for clients.

---

### DEC-023: Deferred Schema

**Date**: 2026-10-08
**Status**: Accepted

`document_versions` (Phase 10), and `page_count`, `word_count` and `metadata` (Phase 4) are not created now, to avoid unused tables and columns. The Phase 0 `storage_path` column is replaced by `storage_key`, and `content_type` and `checksum_sha256` are added to `documents`. `StorageProvider.get() -> bytes` is replaced by streaming `open()`, `save()` now streams and returns a checksum, and `size()` and `delete() -> bool` are added.

---

### DEC-024: Per-User Storage Quota

**Date**: 2026-10-08
**Status**: Accepted

**Context**:
Rate limits and a per-file size cap still allow a registered user to store unbounded data over time (20 uploads a minute of up to 50 MiB each).

**Chosen Option**:
One total-bytes quota per user (`MAX_STORAGE_BYTES_PER_USER`, default 1 GiB), checked from `SUM(file_size)` before the file is stored; over-quota uploads return 403 `QUOTA_EXCEEDED`. Duplicates are reported as duplicates and not charged.

**Tradeoffs**:
Deliberately simple: no per-plan tiers or document-count caps, and the check is not atomic across concurrent uploads (it can be overshot slightly). It is enough to bound disk growth per account.

---

### DEC-025: Lifecycle Reuse for Processing

**Date**: 2026-10-09
**Status**: Accepted

**Chosen Option**:
Keep the Phase 0 statuses. `pending` is "uploaded and queued", `parsing` is "being processed", `ready` means text is extracted and stored. Add two transitions: `parsing -> ready` (a shortcut until chunking, embedding and indexing exist) and `parsing -> pending` (retry or crash recovery).

**Reason**:
Renaming states would churn the schema and API for no behavioural gain; the later stages are already modelled and will take over `parsing -> ready`.

**Consequence**:
`ready` does not yet imply searchable; documentation says so, and Phase 5 will route `parsing` to `chunking`.

---

### DEC-026: Extraction Libraries and Package Layout

**Date**: 2026-10-09
**Status**: Accepted

**Chosen Option**:
PyMuPDF for PDF, python-docx for DOCX, native parsing for text and Markdown, all behind `DocumentExtractor` in `app/parsers/` (the package named in the project layout). No OCR.

**Tradeoffs**:
PyMuPDF is AGPL-3.0 (fine for this open-source portfolio project; a closed deployment needs a commercial licence or another parser behind the same interface). Image-only PDFs cannot be processed.

---

### DEC-027: Conservative, Script-Safe Normalisation

**Date**: 2026-10-09
**Status**: Accepted

**Chosen Option**:
NFC (not NFKC), newline and space normalisation, removal of control and invisible artefacts, preserving ZWJ/ZWNJ, case, punctuation and all combining marks. Inline spaces are collapsed for PDF/DOCX but not for text/Markdown. No lowercasing, stemming, stop-word removal or translation. Normalised output is stamped with a processing version.

**Reason**:
Bengali conjuncts and many Indic and Arabic-script texts depend on joiners and combining marks; compatibility folding (NFKC) would alter fullwidth, ligature and other characters unnecessarily.

**Consequence**:
Composition-excluded Bengali letters (U+09DC, U+09DD, U+09DF) are stored decomposed. Query text must pass through the same normaliser in the retrieval phases.

---

### DEC-028: Section-Based Content Model

**Date**: 2026-10-09
**Status**: Accepted

**Chosen Option**:
A `document_sections` table with one row per PDF page or heading-delimited section, plus counts and a metadata JSONB on `documents`. Blank pages are kept so numbering stays aligned. Sections are replaced atomically on reprocessing.

**Reason**:
Keeps page and heading locations for citations, avoids duplicating whole-document text, and gives the chunker a natural input. Chunks will reference sections rather than copy full text.

---

### DEC-029: Task Design, Retries and Recovery

**Date**: 2026-10-09
**Status**: Accepted

**Chosen Option**:
- One Celery task per document on a dedicated `processing` queue, `acks_late` with `reject_on_worker_lost`, soft/hard time limits and worker recycling.
- A single guarded `UPDATE` claims a document, so duplicates and redeliveries are no-ops. The attempt counter lives in the database.
- Only transient failures retry (exponential backoff, bounded); everything else fails once with a classified reason.
- A periodic sweep run by Celery beat (still Celery, one extra process) releases documents stuck in `parsing`, abandons those out of attempts, and re-queues `pending` documents whose message was lost, including when the broker was down at upload time.
- Task publishing uses `ignore_result` so the API process does not subscribe to result channels.

**Tradeoffs**:
One more container (`celery_beat`, exactly one instance). Time limits are only enforced by the prefork pool; a cooperative deadline covers other pools.

---

### DEC-030: Failure Reporting and Manual Retry

**Date**: 2026-10-09
**Status**: Accepted

**Chosen Option**:
Users see a stable `failure_reason` code and a fixed message; details stay in logs. `POST /documents/{id}/retry` re-queues failed documents with a fresh attempt budget, under the same ownership and administrator policy as other document endpoints, rate limited.

---

### DEC-031: Script Profile Instead of Language Detection

**Date**: 2026-10-09
**Status**: Accepted

**Chosen Option**:
A dependency-free Unicode-block profile (Bengali, Latin, other, with a primary script) is stored in metadata; no language-detection library.

**Reason**:
It reliably separates Bangla, Latin and mixed documents (the distinction later phases need) without a dependency, and avoids pretending to identify languages it cannot.

### DEC-032: A Distinct `chunked` Status

**Date**: 2026-10-09
**Status**: Accepted

**Chosen Option**:
Add `chunked` ("chunks generated, awaiting embedding") between `chunking` and `embedding`; `ready` now strictly means embedded and searchable. Phase 4 documents left in `ready` (text only) were returned to `pending` by migration `0005` and are reprocessed.

**Alternatives**: Keep `ready` for "chunked" (ambiguous once search exists); a separate boolean flag.

**Reason**:
A status that means two things invites a search path that serves unindexed documents. The explicit state keeps the lifecycle checkable by CHECK constraint and transition map.

**Consequence**: documents complete the pipeline in `chunked` until the embedding phase exists; clients must not treat `chunked` as searchable.

### DEC-033: Chunks Never Cross Section Boundaries

**Date**: 2026-10-09
**Status**: Accepted

**Chosen Option**:
Chunk each section independently. A chunk's text is exactly a slice of its section, with page, heading path and offsets stored beside it.

**Alternatives**: Sliding window over the whole document (better filling, but a chunk can span pages and headings, making citations approximate).

**Reason**:
Citations and heading context are a core requirement; an exact, verifiable source location is worth some under-filled chunks.

**Consequence**: very small sections produce small chunks, and a heading-only section produces no chunk (its heading still appears in the heading path of later chunks). Revisit by merging adjacent small sections within a heading if retrieval evaluation shows a problem.

### DEC-034: Hierarchical Splitting with Overlap Only Inside Oversized Blocks

**Date**: 2026-10-09
**Status**: Accepted

**Chosen Option**:
Pack whole paragraph, table and code blocks; split an oversized block by lines, sentences, words and finally a grapheme-safe hard cut. Apply overlap only between consecutive pieces of one oversized block.

**Alternatives**: Fixed-size windows with uniform overlap; a language-specific sentence tokenizer.

**Reason**:
Natural boundaries keep passages coherent and independent of language; overlap across unrelated paragraphs adds noise and storage without recovering split context. The rules are Unicode-based (including the Bengali danda) and need no dependency.

**Consequence**: chunk sizes vary (a median around three quarters of the maximum on the evaluation corpus); Bengali abbreviation-style sentence ends are not special-cased.

### DEC-035: Character-Based Sizing Behind a `SizeMeasure`

**Date**: 2026-10-09
**Status**: Accepted

**Chosen Option**:
Measure and limit chunks in characters (default 1000, overlap 150, minimum 20), via an interface that a token-aware measure can implement later.

**Alternatives**: Count tokens now with the embedding model's tokenizer.

**Reason**:
Deterministic, dependency-free and language-agnostic today; the embedding model is not chosen until the next phase. Bangla produces more tokens per character than English, so the limit must be validated against the real tokenizer then.

**Consequence**: the chunk limit may need to be lowered for Bangla-heavy content or switched to tokens; chunking version changes will mark that.

### DEC-036: Chunk Persistence Is Replace-All and Invariant-Preserving

**Date**: 2026-10-09
**Status**: Accepted

**Chosen Option**:
Persist a document's chunks as a set in the caller's transaction (delete then batched insert, 1000 rows per statement), enforce uniqueness on `(document_id, chunking_version, chunk_index)`, tie chunks to sections with a composite foreign key, and delete a document's chunks whenever it fails or is re-queued.

**Alternatives**: Upsert per chunk; keep old chunks until new ones succeed.

**Reason**:
Replace-all is idempotent and cannot leave a mixture of two runs; batching was about 10x faster than row-by-row inserts for 10,000 chunks in the baseline. Serving a failed document's old chunks would break "chunks exist only for chunked documents".

**Consequence**: re-chunking regenerates chunk IDs, so any later artefact keyed on chunk ID (embeddings) must be rebuilt after re-chunking.

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
