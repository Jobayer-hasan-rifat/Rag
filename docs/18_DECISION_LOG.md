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

### DEC-037: Default Embedding Model: multilingual-e5-small

**Date**: 2026-10-13
**Status**: Accepted

**Chosen Option**:
`intfloat/multilingual-e5-small` (MIT, 384 dimensions, 118M parameters, 94 documented languages including Bengali, about 0.5 GB), pinned to commit `614241f622f53c4eeff9890bdc4f31cfecc418b3`, with the `query: ` / `passage: ` prefixes the model requires.

**Alternatives measured** (46 answerable queries, 48 passages, CPU; `python -m evaluation.compare_models`):

| Model | Dim | Recall@1 | Recall@3 | MRR@10 | Bangla Recall@1 | Passages/s | Query ms |
|-------|----:|---------:|---------:|-------:|----------------:|-----------:|---------:|
| paraphrase-multilingual-MiniLM-L12-v2 | 384 | 0.652 | 0.717 | 0.696 | 0.42 | 147 | 13 |
| **multilingual-e5-small** | 384 | 0.891 | 1.000 | 0.942 | 0.92 | 139 | 14 |
| multilingual-e5-base | 768 | 0.935 | 1.000 | 0.967 | 0.92 | 39 | 38 |
| LaBSE | 768 | 0.957 | 0.957 | 0.957 | 1.00 | 43 | 39 |

Not measured because they are excluded by policy: `BAAI/bge-m3` (the main revision ships only a pickle `pytorch_model.bin`, 2.3 GB) and `Alibaba-NLP/gte-multilingual-base` (needs `trust_remote_code`). The original design default `all-MiniLM-L6-v2` is English-only.

**Reason**:
MiniLM-multilingual does not list Bengali among its languages and measured accordingly (Bangla Recall@1 0.42 against 0.92). e5-small is as fast as it, retrieval-trained, and within noise of the larger models: e5-base's 4-point Recall@1 advantage is two queries out of 46, for 3.5 times the latency and twice the vector storage; LaBSE is trained for translation pairs rather than retrieval, was best on same-language Bangla/English queries but missed a query entirely (Recall@5 0.957) and separates unanswerable queries worst (AUROC 0.920 against 0.982). 384 dimensions also keeps storage small and matches the project's original sizing.

**Consequence / limits**: the dataset is small, synthetic and written by one author; one query is about two percentage points, so differences between e5-small, e5-base and LaBSE are not statistically meaningful, and absolute numbers must not be read as general Bangla retrieval quality. Only the MiniLM result (a large, explained gap) is a clear finding. Re-run the comparison, and ideally a larger real-document evaluation, before relying on the model in production; the provider interface makes a change a configuration edit plus a reindex.

### DEC-038: Separate `chunk_embeddings` and `embedding_configs` Tables

**Date**: 2026-10-13
**Status**: Accepted

**Chosen Option**:
Vectors live in their own table, keyed by `(chunk_id, config_id)`, each referencing a stored embedding configuration, with composite foreign keys that tie the vector to its chunk's document and to the document's owner.

**Alternatives**: an `embedding` column on `document_chunks` (the original design and the Phase 5 plan); one table per model.

**Reason**:
A column cannot hold two models' vectors, so a model upgrade would have to overwrite in place and mix incompatible vectors during the transition. A separate table makes re-embedding explicit, lets old vectors stay until the new set is complete, records exactly which model produced each vector, and keeps the large vector data out of the chunk table that is read for inspection. The owner foreign key lets search filter on the vector table directly while the database guarantees the owner is correct.

**Consequence**: one join per search to reach chunk text; the vector width is fixed by the schema (a different dimension needs a migration).

### DEC-039: Exact Search by Default; HNSW Is Opt-In

**Date**: 2026-10-13
**Status**: Accepted

**Chosen Option**:
No ANN index. Search filters on `(user_id, config_id)` (btree) and sorts exact cosine distances.

**Evidence** (`benchmarks/vector_search_baseline.py`; synthetic clustered 384-d vectors, 100 queries, desktop CPU, pgvector 0.8.7):

| Vectors | Scope | Method | p50 ms | Recall@10 |
|--------:|-------|--------|-------:|----------:|
| 100,000 | one of 20 users (5,000 vectors) | exact | 3.8 | 1.000 |
| 100,000 | one of 20 users | HNSW ef=40 / 100 / 200 | 3.2 / 3.1 / 3.1 | 0.694 / 0.751 / 0.831 |
| 100,000 | one user (all 100,000) | exact | 30.4 | 1.000 |
| 100,000 | one user | HNSW ef=40 / 100 / 200 | 3.3 / 2.1 / 2.4 | 0.615 / 0.799 / 0.909 |
| 10,000 | one user | exact / HNSW ef=200 | 3.8 / 1.1 | 1.000 / 1.000 |

**Reason**: every query is scoped to one user, so the exact scan touches only that user's vectors, is as fast as the ANN index in the multi-user case and always correct. An ANN index with an ownership filter loses recall. The project guidance to index vector columns is met by documenting the threshold and the exact statement (see the schema document) instead of paying the recall and complexity cost early.

Through the real schema (`benchmarks/embedding_baseline.py`, exact search): 2.7 ms for 500 vectors, 6.6 ms for 2,500, 7.7 ms for 10,000 and 23.7 ms for 50,000 owned by one user. An earlier version of the query that joined chunk text and sections before applying the `LIMIT` took 31.9 ms at 10,000 and 76.3 ms at 50,000; the nearest vectors are now found first and the metadata joined only for those rows.

**Consequence**: a single user with several hundred thousand vectors will want HNSW (`SEARCH_HNSW_EF_SEARCH` switches the search to use it); latency is linear in a user's vector count until then. Revisit with measurements on real data.

### DEC-040: Embedding Stage Lifecycle: `embedding -> ready`, Failures Keep Chunks

**Date**: 2026-10-13
**Status**: Accepted

**Chosen Option**:
`chunked -> embedding -> ready`, where `ready` means every chunk has a vector under the active configuration and is the only status search reads. `indexing` stays defined but unused. A failure after chunking keeps the chunks (`failed` with `chunk_count` set), and a manual retry resumes at `chunked`. Re-chunking a `ready` document returns it through `chunking` and drops its vectors with its chunks.

**Alternatives**: keep `embedding -> indexing -> ready`; delete chunks on any failure (the Phase 5 rule); let search read documents that are partly embedded.

**Reason**: the vector index is maintained by PostgreSQL, so an `indexing` step would be empty; keeping chunks after an embedding failure avoids repeating extraction and chunking for a failure that has nothing to do with them; reading only `ready` documents means a document is never half-searchable. The attempt counter counts claims without progress, so large documents embedded over several tasks are not penalised and crash loops still stop.

**Consequence**: the Phase 5 invariant becomes "chunks exist while a document is chunked or beyond, or failed after chunking".

### DEC-041: Dedicated Embedding Worker and Bounded Tasks

**Date**: 2026-10-13
**Status**: Accepted

**Chosen Option**:
A separate `embedding` queue and worker service (concurrency 1, higher memory limit, long-lived processes) with tasks bounded by a time budget that continue themselves. Models are cached in a shared volume; only the embedding worker downloads. The API also loads the model (for query embedding) from the cache, offline, with bounded concurrency.

**Alternatives**: embed in the processing worker (memory, recycling after 50 tasks reloads the model, parses untrusted files next to a loaded model); embed queries by calling the worker (extra latency and moving parts).

**Reason**: isolation and scaling of the memory-heavy component, no model reloads, and a hard bound on any single task keeps Celery time limits and redelivery safe. The API needs the model anyway for queries; a single code path is simpler than a query RPC.

**Consequence**: the API image carries the ML runtime and each API process holds the model in memory (about 0.5 GB for the default); a very small deployment can run one API worker. The first search after a fresh deployment returns 503 until the embedding worker has downloaded the model.

### DEC-042: Search API Shape

**Date**: 2026-10-13
**Status**: Accepted

**Chosen Option**:
`POST /api/v1/search/semantic` with a JSON body; only the caller's own `ready` documents (administrators too); `score` is cosine similarity with `distance` also returned; the query is normalised like document text; per-user rate limit; 128 KiB body cap; `top_k` and query length bounded by configuration.

**Reason**: a POST keeps queries (which may be sensitive) out of URLs and logs; normalising queries with the document normaliser makes composed and decomposed Unicode match; exposing the metric rather than a "relevance percentage" avoids implying a calibration the model does not have.

**Consequence**: administrators cannot search other users' documents through this endpoint (a deliberate choice that differs from by-id access); the 404-for-unowned-filter rule is the same as for documents.

### DEC-043: Re-embedding Is Explicit

**Date**: 2026-10-13
**Status**: Accepted

**Chosen Option**:
A changed embedding configuration never triggers re-embedding by itself. The operator runs the `reindex_embeddings` task; each document is re-embedded under the new configuration and its old vectors are deleted when the new set is complete. Documents without vectors under the active configuration are not returned by search.

**Reason**: automatic mass re-embedding after a settings typo would burn hours of compute and silently change results; mixing vectors of two models would corrupt rankings. An explicit step with a visible reduced-results window is safer.

**Consequence**: documents are unsearchable between a model change and their re-embedding; run the task right after the change. Vectors of the old configuration are not kept for rollback (re-run the task with the old settings to roll back).

### DEC-044: Model Supply-Chain Rules and Heading Context

**Date**: 2026-10-13
**Status**: Accepted

**Chosen Option**:
The model revision must be a full commit hash (settings validation refuses a branch name); weights load from safetensors only; remote code is never trusted; downloads happen only in the embedding worker or the explicit `python -m app.embeddings.download` command. Heading context (prepending the heading path to each chunk before embedding) is available (`EMBEDDING_INCLUDE_HEADING`) but off by default because it gave no consistent gain in measurement (Recall@1 0.891 to 0.913 for e5-small, 0.935 to 0.913 for e5-base); it is part of the configuration fingerprint, so changing it is a reindex.

**Reason**: an unpinned or pickle-based model is code execution on the worker; the measured effect of heading context was within noise and would have added input length and a reindex for no demonstrated benefit.

---

### DEC-045: Sparse Retrieval Uses PostgreSQL Full-Text Search with the `simple` Configuration

**Date**: 2026-10-13
**Status**: Accepted

**Chosen Option**: a generated `tsvector` column (`to_tsvector('simple', text)`) on `document_chunks`.

**Alternatives**: an external search engine (another service to secure and keep consistent), a BM25 extension (not available in the stock image), the `english` configuration (stems English only and would corrupt Bengali handling).

**Reason**: PostgreSQL ships no Bengali dictionary, so `simple` (lowercase, no stemming, no stop words) is the only configuration that treats Bengali and English alike; measured, its default parser keeps Bengali words and conjuncts whole under a UTF-8 locale (the migration verifies this). The honest consequence is that inflected Bengali forms (`বাংলাদেশ` / `বাংলাদেশের`) do not match lexically; the dense retriever covers them.

**Consequences**: no stemming or synonyms; identifiers such as `ERR-4721` are indexed as `err` and `-4721`, so exact identifiers rank correctly while a spaced `ERR 4721` cannot tell near codes apart.

---

### DEC-046: Per-Owner Composite GIN Index Through a Denormalised `user_id`

**Date**: 2026-10-13
**Status**: Accepted

**Chosen Option**: add `document_chunks.user_id` (trigger-filled, composite foreign key to `documents (id, user_id)`) and index `(user_id, search_vector)` with GIN through `btree_gin`.

**Alternatives**: a plain GIN index joined to `documents` for the owner filter; no index.

**Reason**: measured with 100,000 chunks (`benchmarks/sparse_index_probe.py`): composite index 5.7 ms, plain GIN plus join 261 ms (worse than the 112 ms sequential scan), no index 112 ms. The same `user_id` copy pattern is already used for `chunk_embeddings`; the foreign key makes the copy impossible to get wrong.

**Consequences**: one extra column and a trigger; inserts that do not name the owner keep working.

---

### DEC-047: Lexical Scoring Weights Terms by Inverse Document Frequency in the Caller's Own Corpus

**Date**: 2026-10-13
**Status**: Accepted

**Chosen Option**: score a chunk by the sum of the inverse document frequencies of the matched query terms (computed in SQL over the caller's own `ready` chunks), using the rarest `SPARSE_MAX_TERMS` terms and phrases through `phraseto_tsquery`.

**Alternatives**: `ts_rank` / `ts_rank_cd` (no inverse document frequency, so common words outweigh a rare identifier).

**Reason**: a rare identifier must outrank a chunk full of common words; `ts_rank` cannot do that. Computing frequencies inside the caller's corpus also keeps scores independent of other users' data, which is a security property and not only a ranking one.

---

### DEC-048: Reciprocal Rank Fusion with `k = 60` and Equal Weights, Not Tuned

**Date**: 2026-10-13
**Status**: Accepted

**Chosen Option**: fuse the two rankings with Reciprocal Rank Fusion (`score = sum of weight / (k + rank)`), `k = 60`, weights 1.0, 50 candidates from each retriever; all configurable.

**Alternatives**: weighted score combination (requires normalising cosine similarity and a lexical score onto one scale, which is fragile), learned fusion (no training data).

**Reason**: rank-based fusion needs no calibration. The defaults are the published ones. A sweep over `k` and weights on the evaluation set (reported in the benchmarks) showed that a dense-heavy weighting looked slightly better, but the sweep ran on the same 77 answerable queries it would be tuned on, so the neutral defaults were kept; the sweep is evidence for a future larger evaluation, not a basis for a change today.

**Consequences**: on the evaluation set hybrid retrieval did **not** beat dense retrieval overall (MRR 0.931 against 0.940); it was better on queries that need exact words or phrases and worse on some paraphrase and cross-language queries. The numbers and their limits are recorded with the benchmarks.

---

### DEC-049: Function Words Are Dropped From the Lexical Query

**Date**: 2026-10-13
**Status**: Accepted

**Chosen Option**: English and Bengali function words (`the`, `is`, `কী`, `কেন`, ...) are removed from the free words of the lexical query (not from quoted phrases, and never from the index); `SPARSE_REMOVE_STOPWORDS=false` restores plain matching.

**Reason**: the first evaluation run showed the lexical retriever matching questions on `what`, `is`, `the` and similar words, filling its candidate list with noise that Reciprocal Rank Fusion then placed above correct dense results (cross-language queries fell from 0.71 to 0.03 MRR). Removing function words recovered most of the loss (hybrid MRR 0.874 to about 0.92-0.93; runs differed by about 0.01 because ties were broken by random chunk ids, which the evaluation now fixes). The lists are short, conventional lists, not derived from the evaluation queries, but the change was motivated by looking at the evaluation, so the post-change numbers are slightly optimistic; both runs are kept.

**Consequences**: a query made only of function words has no lexical candidates (quote it to force a phrase match).

---

### DEC-050: Hybrid Endpoint Shape and Partial Failure

**Date**: 2026-10-13
**Status**: Accepted

**Chosen Option**: a new endpoint `POST /api/v1/search/hybrid` next to the unchanged semantic endpoint. The response reports each result's rank in each retriever, diagnostics (candidate counts, overlap), and which retriever was degraded. If one retriever fails the other answers; both failing is a 503.

**Alternatives**: a `mode` parameter on the semantic endpoint (changes its contract and score meaning), failing the request when either retriever fails (less available).

**Reason**: clients of `/search/semantic` keep a stable contract and score meaning; retrieval stays useful when the model is unavailable.

---

### DEC-051: Reranking Is a Separate, Optional, Per-Request Layer

**Date**: 2026-10-13
**Status**: Accepted

**Chosen Option**: `app/reranking/` (types, ordering, cross-encoder, registry) independent of the embedding provider, switched on by `RERANK_ENABLED` (default off) and requested per call with `rerank: true` on `POST /api/v1/search/hybrid`.

**Alternatives**: a `Reranker` as another embedding provider (a cross-encoder scores pairs and produces no vector); a new endpoint; always-on reranking.

**Reason**: the semantic endpoint and default hybrid behaviour must stay byte-for-byte as they were so the baseline remains comparable; a request-level flag lets a client A/B the two on the same data.

---

### DEC-052: Default Reranker `BAAI/bge-reranker-v2-m3`

**Date**: 2026-10-13
**Status**: Accepted

**Chosen Option**: bge-reranker-v2-m3 (Apache-2.0, XLM-RoBERTa, safetensors, standard architecture, pinned revision `953dc6f6...`).

**Alternatives**: `mmarco-mMiniLMv2-L12-H384-v1` (5x smaller and 10x faster, but trained on 13 languages without Bengali); three models refused for custom code, a non-commercial licence or pickle-only weights.

**Reason**: measured on the Phase 7 dataset (selection happened on this set, so it is not a blind result): bge raised hybrid MRR@10 from 0.931 to 0.981 with 10 candidates and improved cross-language, confusable and mixed-script queries, while the small model improved the base set but made the lexical set and paraphrase queries worse (MRR 0.74 for paraphrase) and was worse than no reranking with 30 candidates. A separate, smaller held-out set (24 passages, 30 answerable queries) was used once afterwards; there the baselines were already near the ceiling (dense MRR 1.000) so it confirms "no harm" for bge and does not demonstrate improvement.

**Consequences**: about 2.3 GB of weights and resident memory and about 1 s of CPU time for 10 candidates. Reranking is therefore off by default and the small model remains configurable. No claim of general Bangla quality is made.

---

### DEC-053: Inference in the API Process, Not a Queue

**Date**: 2026-10-13
**Status**: Accepted

**Chosen Option**: score in a worker thread of the API process, behind a concurrency gate (`RERANK_MAX_CONCURRENT`, default 1), a deadline checked between batches and a request timeout; the database connection is released first.

**Alternatives**: a Celery reranking worker.

**Reason**: reranking is interactive (about 1 s), and a queue adds a broker round trip, a result wait and a second resident copy of a 2.3 GB model without removing the CPU cost. If measurements later show contention with search, a dedicated service is the next step.

---

### DEC-054: Raw Scores, Bounded Candidates, Documented Truncation

**Date**: 2026-10-13
**Status**: Accepted

**Chosen Option**: the reranker's raw output (logit) is returned as `rerank_score` with no softmax or normalisation; default 10 candidates (cap 100); query and passage share a 512-token budget and the end of a longer passage is dropped and flagged per result (`rerank_truncated`).

**Reason**: a sigmoid would suggest a probability the model does not guarantee; scores are query-dependent and never stored. Ten candidates were as good as thirty on the evaluation set (MRR 0.981 against 0.976) at a third of the cost. Chunks are at most about 1,000 characters, so truncation was not observed in the evaluation (0 pairs) but can occur for dense scripts.

---

### DEC-055: Reranking Failure Falls Back Visibly

**Date**: 2026-10-13
**Status**: Accepted

**Chosen Option**: if the model is missing, busy, slow, failing or returns unusable output, the response is 200 with the hybrid order, `rerank.applied: false`, a reason and `degraded: ["rerank"]`; asking for reranking on a server where it is disabled is a 422.

**Alternatives**: failing the request (less available); silently returning the hybrid order (misleading).

**Reason**: reranking is an enhancement of a working retrieval, but the caller must be able to tell what it received.

---

### DEC-056: Generation Providers: Local Ollama and OpenAI-Compatible HTTP

**Date**: 2026-10-13
**Status**: Accepted

**Chosen Option**: a small async `LLMProvider` interface with two `httpx` implementations: `ollama` (local, default for development) and `openai_compatible` (hosted or self-hosted, explicit opt-in).

**Alternatives**: running models in-process with transformers (large weights, long load, memory in the API process); vendor SDKs (extra dependencies, lock-in); a single hosted provider.

**Reason**: a local server keeps documents on the machine and needs no paid API; one OpenAI-compatible adapter covers OpenAI, LM Studio, vLLM and most gateways. The application never downloads or loads model files itself, so the model-trust rules of earlier phases are not weakened.

---

### DEC-057: Prompt Structure and Delimiting

**Date**: 2026-10-13
**Status**: Accepted

**Chosen Option**: instructions in a versioned file (`answer_v1`) sent as the system message; the question and sources in the user message as `<question>` and `<source id="S1" document=... page=... heading=...>` blocks with XML-escaped text and neutralised marker-like strings.

**Reason**: CLAUDE.md requires prompts outside the code and versioned; escaping gives a stable boundary the model can rely on and sources cannot break. It is a mitigation, not a guarantee.

---

### DEC-058: Citation Contract: Labels, Database-Built Citations, Local Validation

**Date**: 2026-10-13
**Status**: Accepted

**Chosen Option**: per-request labels `S1..Sn`; the model writes inline `[S1]` markers and a `citations` list; the application validates the output locally and builds every citation object from the database record behind a valid label, with a bounded excerpt of what the model saw. Unknown labels are removed; sentences without a valid label are dropped; if the model lists sources but writes no inline markers the listed labels apply to the answer as a whole and the response says so (`citation_granularity: answer`).

**Alternatives**: trusting provider structured output (not available for every model; models violated the schema in testing: a code fence, a missing field, labels as `[S1]`); asking the model for page numbers or quotations (fabrication risk).

**Reason**: provider guarantees are not a security property; a first evaluation run showed one model filling the `citations` array but omitting inline markers in most answers, which strict sentence-level validation turned into false abstentions; the answer-level fallback keeps those answers and states the weaker guarantee.

---

### DEC-059: Deterministic Support Checks (Quotations and Literals)

**Date**: 2026-10-13
**Status**: Accepted

**Chosen Option**: after label validation, a sentence is dropped if it contains a double-quoted passage that is not in a single cited source, or a number or identifier (any script's digits folded to ASCII, matched as a whole token) that is not in the cited sources.

**Reason**: these are the claims most damaging when invented and the only ones checkable without a second model. The independent review found and fixed substring matching, non-Latin digits, spliced quotations and a text-mutating clean-up.

**Consequences**: sentences without digits or quotes are only checked for having a valid label; a real number attached to a wrong claim passes; a correct answer that computes or spells out a number can be dropped. No model-as-judge is used anywhere.

---

### DEC-060: Context Budget Measured on the Rendered Prompt

**Date**: 2026-10-13
**Status**: Accepted

**Chosen Option**: token estimates are conservative (ASCII 3 characters per token, every other character one token) and are applied to the escaped body plus the actual opening tag of each block; the total is bounded by `RAG_CONTEXT_TOKEN_BUDGET` and by the model window minus output, system prompt and question; settings must fit `LLM_CONTEXT_TOKENS`; the provider's reported `input_tokens` is checked against the window (a prompt that filled it is rejected).

**Reason**: the application has no tokenizer for the generation model; a silent truncation by the provider could remove the instructions. The review showed a fixed per-block overhead and unescaped measurement could overflow the window.

---

### DEC-061: Generation in the API Process Behind a Gate; No Queue

**Date**: 2026-10-13
**Status**: Accepted

**Chosen Option**: the request calls the provider directly (async HTTP) after releasing the database connection, behind a concurrency gate (`LLM_MAX_CONCURRENT`, default 1) with a queue timeout and the provider timeout.

**Reason**: the model runs in another process; the API only waits on I/O, so no worker is needed. A local CPU-bound model serialises anyway, so concurrency 1 is the honest default.

---

### DEC-062: Failures Are Explicit; Insufficient Evidence Is a Normal Answer

**Date**: 2026-10-13
**Status**: Accepted

**Chosen Option**: provider timeouts, outages, rate limits and busy slots are 503 `GENERATION_UNAVAILABLE` with a reason; unusable output (after one retry) or a window-filling prompt is 502 `GENERATION_FAILED`; no retrieval result, a model abstention or a fully rejected answer is a 200 with `insufficient_evidence: true` and a fixed message; retrieval degradation is reported separately from the answer.

**Reason**: an answer service must never fabricate when its parts fail, and callers must be able to tell "no evidence" from "service problem".

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
