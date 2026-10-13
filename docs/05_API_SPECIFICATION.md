# API Specification

## Overview

This document defines the complete API specification for the Intelligent Document Processing & RAG Platform. The API follows RESTful conventions with JSON request/response formats.

**Base URL**: `/api/v1`

**Authentication**: JWT Bearer token (except where noted)

## Common Patterns

### Authentication Header

```
Authorization: Bearer <access_token>
```

### Response Format

#### Success Response

```json
{
  "data": { ... },
  "meta": {
    "request_id": "uuid",
    "timestamp": "2026-10-07T20:26:49.575Z"
  }
}
```

#### Error Response

```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable message",
    "details": { ... }
  },
  "meta": {
    "request_id": "uuid",
    "timestamp": "2026-10-07T20:26:49.575Z"
  }
}
```

### Pagination

```json
{
  "data": [ ... ],
  "meta": {
    "page": 1,
    "page_size": 20,
    "total_items": 100,
    "total_pages": 5,
    "request_id": "uuid",
    "timestamp": "2026-10-07T20:26:49.575Z"
  }
}
```

### HTTP Status Codes

| Code | Meaning | Usage |
|------|---------|-------|
| 200 | OK | Successful GET, PUT, PATCH |
| 201 | Created | Successful POST |
| 204 | No Content | Successful DELETE |
| 400 | Bad Request | Validation error |
| 401 | Unauthorized | Missing or invalid token |
| 403 | Forbidden | Insufficient permissions |
| 404 | Not Found | Resource doesn't exist |
| 409 | Conflict | Resource conflict (e.g., duplicate) |
| 422 | Unprocessable Entity | Validation failure |
| 500 | Internal Server Error | Server error |

---

## Authentication Endpoints

All responses use the standard envelope (`data` + `meta`, or `error` + `meta`). Request bodies
reject unknown fields (HTTP 422), so clients cannot submit fields such as `role`.
Authenticated endpoints expect `Authorization: Bearer <access_token>`; OpenAPI declares this as
the `BearerAuth` security scheme.

### POST /auth/register

Create an account with the default `user` role.

**Authentication**: None. **Rate limited** (per client IP).

**Request Body**:
```json
{
  "email": "user@example.com",
  "password": "SecurePassword123",
  "display_name": "John Doe"
}
```

**Validation**:
- `email`: valid address; trimmed and lowercased before use
- `password`: 8-72 bytes, at least one uppercase letter, one lowercase letter and one digit
- `display_name`: 1-100 characters after trimming, no control characters

**Response** (201):
```json
{
  "data": {
    "id": "uuid",
    "email": "user@example.com",
    "display_name": "John Doe",
    "role": "user",
    "is_active": true,
    "created_at": "2026-10-08T10:00:00Z"
  },
  "meta": {"request_id": "uuid", "timestamp": "2026-10-08T10:00:00Z"}
}
```

**Errors**:
- 409 `CONFLICT`: "Unable to register with the provided details" (deliberately does not say what clashed)
- 422 `VALIDATION_ERROR`: field-level messages; the submitted password is never echoed
- 429 `RATE_LIMIT_EXCEEDED`

---

### POST /auth/login

Exchange credentials for tokens.

**Authentication**: None. **Rate limited** (per client IP and per account).

**Request Body**: `{"email": "user@example.com", "password": "SecurePassword123"}`

**Response** (200, `Cache-Control: no-store`):
```json
{
  "data": {
    "access_token": "eyJ...",
    "refresh_token": "opaque-random-string",
    "token_type": "bearer",
    "expires_in": 900
  },
  "meta": {"request_id": "uuid", "timestamp": "2026-10-08T10:00:00Z"}
}
```

**Errors**:
- 401 `AUTHENTICATION_ERROR`: "Invalid email or password". Returned identically for an unknown
  account, a wrong password, a deactivated account and a deleted account.
- 422 `VALIDATION_ERROR`, 429 `RATE_LIMIT_EXCEEDED` (with `Retry-After`)

---

### POST /auth/refresh

Rotate a refresh token. The presented token is consumed and a new access and refresh token are returned.

**Authentication**: None (the refresh token is the credential). **Rate limited**.

**Request Body**: `{"refresh_token": "opaque-random-string"}`

**Response** (200, `Cache-Control: no-store`): same shape as login.

**Errors**:
- 401 `AUTHENTICATION_ERROR`: unknown, expired or revoked token. Presenting a token that was
  already used revokes the entire session (token family).
- 422, 429

---

### POST /auth/logout

End the current session.

**Authentication**: Required (access token).

**Request Body**: `{"refresh_token": "opaque-random-string"}`

**Response**: 204 No Content. The refresh-token family is revoked (only if it belongs to the
caller) and the presented access token is denylisted until it would have expired. The response
is the same whether or not the refresh token was recognised.

**Errors**: 401 (missing/invalid access token), 422, 503 (revocation could not be recorded; retry)

---

### GET /auth/me

Return the authenticated user.

**Authentication**: Required.

**Response** (200):
```json
{
  "data": {
    "id": "uuid",
    "email": "user@example.com",
    "display_name": "John Doe",
    "role": "user",
    "is_active": true,
    "created_at": "2026-10-08T10:00:00Z"
  },
  "meta": {"request_id": "uuid", "timestamp": "2026-10-08T10:00:00Z"}
}
```

**Errors**: 401 `AUTHENTICATION_ERROR` (missing, malformed, expired, revoked token, or deactivated user), 503 (token revocation list unavailable)

---

## User Endpoints

Planned (not implemented): `PATCH /users/me` to change `display_name` and password. Changing a
password will revoke all of the user's refresh tokens.

---

## Collection Endpoints

All endpoints require authentication. A collection that does not exist and one that belongs to someone
else are indistinguishable: both return 404. Administrators may act on any collection by ID (FR-1.4);
listings always show only the caller's own.

### GET /collections

List your collections, ordered by name.

**Query parameters**: `page` (default 1), `page_size` (default 20, max 100), `search` (name substring, case-insensitive, max 100 characters).

**Response** (200): `{"data": [Collection], "meta": {"page", "page_size", "total_items", "total_pages", "request_id", "timestamp"}}`

Collection: `{"id", "name", "description", "document_count", "created_at", "updated_at"}`

---

### POST /collections

**Request**: `{"name": "Research Papers", "description": "optional"}` (name 1-255 characters, whitespace normalised; unknown fields rejected)

**Response**: 201 with the Collection. **Errors**: 409 `CONFLICT` (you already have a collection with that name, case-insensitively), 422.

---

### GET /collections/{id} / PATCH /collections/{id} / DELETE /collections/{id}

- `GET` returns the Collection. `PATCH` accepts `name` and/or `description` (`null` clears the description; an empty body is a 422). `DELETE` returns 204 and keeps the collection's documents.
- **Errors**: 404 (missing or not yours), 409 (duplicate name on PATCH), 422.

---

### POST /collections/{id}/documents

Add your documents to a collection. **Request**: `{"document_ids": ["uuid", ...]}` (1-100 ids).
**Response** (200): `{"data": {"added_count": 2, "already_exists_count": 0}}`.
**Errors**: 404 if the collection or any document is missing or not yours (nothing is added), 422.

### DELETE /collections/{id}/documents/{document_id}

Remove a document from a collection (204). **Errors**: 404 if the collection or membership does not exist.

---

## Document Endpoints

All endpoints require authentication and enforce ownership server-side. Responses never contain storage
keys, filesystem paths or owner IDs. A document that is missing and one that belongs to someone else both
return 404 (administrators may access any document by ID, but `GET /documents` only lists the caller's own).

Document: `{"id", "filename", "file_type", "content_type", "file_size", "checksum_sha256", "status", "error_message", "failure_reason", "processing_started_at", "processing_completed_at", "page_count", "character_count", "chunk_count", "chunking_version", "embedded_chunk_count", "embedding_completed_at", "searchable", "collections": [{"id","name"}], "created_at", "updated_at"}`

**Processing state**: uploads start as `pending` and are processed asynchronously. `status` moves `pending -> parsing -> chunking -> chunked -> embedding -> ready` (or `failed`); `chunked` means the text was split into chunks and the document awaits embedding, `embedding` means vectors are being generated (`embedded_chunk_count` of `chunk_count` so far), and `ready` means the document is searchable (`searchable` is true exactly then). `page_count` is set for PDFs, `character_count` once text is stored, and `chunk_count` and `chunking_version` once chunks are stored (otherwise `null`). For `failed` documents `failure_reason` is a stable code (`corrupt_document`, `encrypted_document`, `empty_document`, `too_many_pages`, `content_too_large`, `too_many_chunks`, `chunking_failed`, `model_unavailable`, `embedding_failed`, `invalid_embedding`, `timeout`, `storage_missing`, `storage_unavailable`, `extraction_failed`, `database_error`, `retries_exhausted`) and `error_message` a fixed, safe sentence. Stack traces, paths and exception text are never returned. Poll `GET /documents/{id}` for progress.

### GET /documents

**Query parameters**: `page` (default 1), `page_size` (default 20, max 100), `status`, `file_type` (`pdf|docx|txt|md`), `collection_id`, `search` (filename substring), `sort` (`created_at|filename|file_size`, default `created_at`), `order` (`asc|desc`, default `desc`).

Ordering is stable (ties are broken by id). Unknown sort fields, filter values or out-of-range pagination return 422. Filtering by a collection that is not yours returns 404.

**Response** (200): `{"data": [Document], "meta": {pagination..., "request_id", "timestamp"}}`

---

### POST /documents

Upload a document as `multipart/form-data`.

**Fields**: `file` (required, exactly one) and `collection_ids` (optional JSON array of your collection UUIDs, max 20). Any other form field is rejected (400).

**Behaviour**: authentication and the per-user upload rate limit are checked before the body is read; the body is
capped (configured maximum plus 1 MiB of multipart overhead) before it is buffered; the extension, declared content type and the
actual content are validated (see the security document); a SHA-256 checksum is computed; the file is streamed
into storage under a generated key; then the record is created. If the record cannot be saved, the stored file is removed.

**Response** (201): the Document with `"status": "pending"`. Processing is queued and runs in the background; the request never waits for it.

**Errors**:
- 401 authentication required; 429 `RATE_LIMIT_EXCEEDED` (default 20 uploads per user per minute, `Retry-After` set)
- 404 a listed collection is missing or not yours (nothing is stored)
- 403 `QUOTA_EXCEEDED` the upload would exceed your storage quota (`details.quota_bytes`; default 1 GiB)
- 409 `DUPLICATE_DOCUMENT` you already uploaded identical content (`details.existing_document_id`)
- 413 `FILE_TOO_LARGE` (`details.max_bytes`); 415 `UNSUPPORTED_FILE_TYPE` (extension, declared type or content not acceptable)
- 422 `INVALID_FILE` (empty file, unusable filename) or `VALIDATION_ERROR` (missing `file`, bad `collection_ids`)
- 503 `STORAGE_UNAVAILABLE` (nothing was saved; retry)

---

### GET /documents/{id}

Metadata for one document. **Errors**: 404.

### PATCH /documents/{id}

Rename the display name: `{"filename": "New name.pdf"}`. The name is sanitised and its extension must match the
document's type (415 otherwise). The stored file is untouched. **Errors**: 404, 415, 422.

### GET /documents/{id}/chunks

Inspect a document's chunks in reading order (for debugging and for verifying how a document was split). Only the
owner can read them (administrators may, per the document access policy); another user's document and a missing one
both return 404.

**Query parameters**: `page` (default 1), `page_size` (default 20, max 100).

**Response** (200): a paginated list of `{"id", "chunk_index", "text", "char_count", "section_ordinal", "page_number", "heading", "heading_level", "heading_path": [..], "start_char", "end_char", "overlap_chars"}`. `start_char` and `end_char` are offsets into the source section and `end_char - start_char == char_count`. A document without chunks returns an empty list. Storage keys, section/document IDs and owner IDs are never returned.

**Errors**: 401; 404 (missing or not yours); 422 (invalid pagination or ID).

### POST /documents/{id}/retry

Re-queue a **failed** document with a fresh attempt budget. If the failure happened after chunking (an embedding failure) the document resumes at `chunked` and only the embedding stage runs again; otherwise it restarts at `pending`. Rate limited per user (same budget as uploads).

**Response** (202): the Document, now `pending` (or `chunked`) with `failure_reason` and `error_message` cleared.

**Errors**: 401; 404 (missing or not yours); 409 `INVALID_STATE_TRANSITION` if the document is not `failed`; 429.

### GET /documents/{id}/download

Streams the original file. Headers: `Content-Disposition: attachment` (ASCII fallback plus RFC 5987 UTF-8 name),
`Content-Type` set by the server, `Content-Length`, `X-Content-Type-Options: nosniff`,
`Cache-Control: private, no-store`, `Content-Security-Policy: default-src 'none'; sandbox`.
No public URL is ever created.

**Errors**: 404; 500 `STORAGE_INCONSISTENCY` if the record exists but its stored file is missing or its size no longer matches (logged at ERROR);
503 `STORAGE_UNAVAILABLE`.

### DELETE /documents/{id}

Deletes the stored file, then the record and its collection links (204). A file that is already missing is
logged at ERROR and does not block deletion; a storage failure returns 503 and keeps the record so the call can be retried.
**Errors**: 404, 503.

---

### Planned

`POST/GET /documents/{id}/versions` arrive with versioning in Phase 10.

---

## Search Endpoints

### POST /search/semantic

Semantic search over **your own searchable (`ready`) documents**. The query is embedded with the same model the
documents were embedded with and matched by cosine similarity. The query is sent in the body, never in the URL, so
it does not end up in access logs.

**Authentication**: Required. Rate limited per user (`RATE_LIMIT_SEARCH_ATTEMPTS` per window, default 60).

**Request body** (JSON, at most 128 KiB):
```json
{
  "query": "ইলিশ ধরা কবে নিষিদ্ধ?",
  "top_k": 10,
  "document_ids": ["uuid1", "uuid2"],
  "collection_id": "uuid3",
  "min_score": 0.8
}
```

| Field | Rules |
|-------|-------|
| `query` | Required text in any language. Normalised like document text (NFC, whitespace); at most `SEARCH_MAX_QUERY_CHARS` (default 1,000) after normalisation; empty or whitespace-only is rejected. |
| `top_k` | Optional, 1 to `SEARCH_MAX_TOP_K` (default 50); default `SEARCH_DEFAULT_TOP_K` (10). |
| `document_ids` | Optional, at most 100. Every id must be one of your documents, otherwise **404** (indistinguishable from a missing document). |
| `collection_id` | Optional. Must be your collection, otherwise 404. |
| `min_score` | Optional, -1 to 1. Drops results whose `score` is lower. |

**Response** (200):
```json
{
  "data": {
    "results": [
      {
        "rank": 1,
        "chunk_id": "uuid",
        "document_id": "uuid",
        "document_name": "hilsa.pdf",
        "chunk_index": 3,
        "section_ordinal": 1,
        "page_number": 2,
        "heading": "নিষেধাজ্ঞা",
        "heading_level": 2,
        "heading_path": ["ইলিশ মাছ", "নিষেধাজ্ঞা"],
        "text": "ডিম ছাড়ার মৌসুমে ...",
        "start_char": 0,
        "end_char": 118,
        "score": 0.8412,
        "distance": 0.1588
      }
    ],
    "search": {
      "embedding_model": "intfloat/multilingual-e5-small",
      "model_revision": "614241f622f53c4eeff9890bdc4f31cfecc418b3",
      "metric": "cosine",
      "score_type": "cosine_similarity",
      "top_k": 10,
      "returned": 1,
      "searched_documents": "own, ready",
      "latency_ms": 31
    }
  },
  "meta": {"request_id": "uuid", "timestamp": "2026-10-13T00:00:00Z"}
}
```

**Score semantics.** `score = 1 - distance`, the cosine similarity between the query vector and the chunk vector,
in [-1, 1] (`distance` is the cosine distance, 0 to 2). Results are ordered best first. The score is a **ranking
signal for one query and one model**: it is not a probability, not a percentage of relevance, and not comparable
across models or languages. Some models compress scores into a narrow band (the default e5 model gives roughly
0.7-0.9 even for unrelated text), so a fixed `min_score` needs to be calibrated on your own data; the number of
results is not an indication of relevance. An empty `results` list means nothing is searchable yet or `min_score`
removed everything.

**Scope.** Only the caller's own documents are searched, administrators included (consistent with `GET /documents`).
Documents that are not `ready` (still processing, mid-embedding, failed, or embedded under an older model) are not
searched. `section_ordinal`, `page_number` and `heading_path` locate the chunk in its source; `start_char` and
`end_char` are offsets into the section text.

**Errors**: 401; 404 (a filtered document or collection is not yours or does not exist); 413 `REQUEST_TOO_LARGE`; 422
`VALIDATION_ERROR` (invalid or too long query, `top_k` above the limit, malformed filters); 429
`RATE_LIMIT_EXCEEDED` (`Retry-After` header); 503 `EMBEDDING_UNAVAILABLE` (the model cannot load, timed out, or the
server is saturated; retry later). Error bodies never contain paths, stack traces or model internals.

---

### POST /search/keyword

Keyword search using PostgreSQL full-text search.

**Authentication**: Required

**Request Body**:
```json
{
  "query": "machine learning",
  "top_k": 10,
  "filters": {
    "document_ids": ["uuid1"],
    "collection_ids": ["uuid2"]
  }
}
```

**Response** (200):
```json
{
  "data": {
    "results": [
      {
        "chunk_id": "uuid",
        "document_id": "uuid",
        "document_name": "research_paper.pdf",
        "content": "Machine learning algorithms are...",
        "page_number": 5,
        "rank": 0.45,
        "metadata": {}
      }
    ],
    "total_results": 8,
    "query_time_ms": 12
  },
  "meta": {
    "request_id": "uuid",
    "timestamp": "2026-10-07T20:26:49.575Z"
  }
}
```

---

### POST /search/hybrid

Hybrid search: dense (vector) retrieval and lexical (PostgreSQL full-text) retrieval, fused with Reciprocal Rank
Fusion. Implemented in Phase 7; `POST /search/semantic` is unchanged.

**Authentication**: Required. Rate limited with the same per-user search budget as `/search/semantic`.

**Request Body**:
```json
{
  "query": "ERR-4721 payment worker",
  "top_k": 10,
  "document_ids": ["uuid1"],
  "collection_id": "uuid2"
}
```

`query` is required (at most `SEARCH_MAX_QUERY_CHARS` characters after whitespace normalisation). `top_k` defaults to
`SEARCH_DEFAULT_TOP_K` and is at most `SEARCH_MAX_TOP_K`. `document_ids` and `collection_id` restrict both retrievers; a
document or collection that is not yours is a 404, identical to one that does not exist. Wrap words in double quotes
(`"queue connection"`) to require them as an adjacent phrase. The body is capped at 128 KiB.

**Response** (200):
```json
{
  "data": {
    "results": [
      {
        "rank": 1,
        "chunk_id": "uuid",
        "document_id": "uuid",
        "document_name": "runbook.md",
        "chunk_index": 3,
        "section_ordinal": 2,
        "page_number": null,
        "heading": "Payments",
        "heading_level": 2,
        "heading_path": ["Incident Runbook", "Payments"],
        "text": "Error ERR-4721 means the payment worker lost its queue connection...",
        "start_char": 0,
        "end_char": 160,
        "score": 0.03279,
        "dense_rank": 1,
        "sparse_rank": 1,
        "dense_score": 0.874
      }
    ],
    "search": {
      "mode": "hybrid",
      "fusion": "rrf",
      "rrf_k": 60,
      "top_k": 10,
      "returned": 1,
      "dense_candidates": 50,
      "sparse_candidates": 12,
      "overlap": 8,
      "retrievers_used": ["dense", "sparse"],
      "degraded": [],
      "embedding_model": "intfloat/multilingual-e5-small",
      "model_revision": "614241f622f53c4eeff9890bdc4f31cfecc418b3",
      "searched_documents": "own, ready",
      "latency_ms": 31
    }
  },
  "meta": {"request_id": "uuid", "timestamp": "2026-10-13T00:00:00Z"}
}
```

**Score semantics.** `score` is the Reciprocal Rank Fusion score, the sum over the retrievers that returned the chunk
of `weight / (k + rank)` (default `k = 60`, weights 1.0). It depends only on ranks: it is **not** a probability, not a
cosine similarity, and not comparable between queries. Its maximum with two retrievers is `2 / (k + 1)`. `dense_rank`
and `sparse_rank` are the chunk's position in each retriever (`null` when that retriever did not return it);
`dense_score` is the cosine similarity of the dense retriever and is the only value here with a similarity meaning.
Ties are broken deterministically.

**Lexical retrieval.** Words are matched exactly after case folding: PostgreSQL has no Bengali dictionary, so there is
no stemming (`বাংলাদেশ` does not match `বাংলাদেশের`) and no synonyms. English and Bengali function words are ignored in
the free words (not in quoted phrases). Query terms are weighted by how rare they are in the caller's own documents,
and at most `SPARSE_MAX_TERMS` of the rarest are used; the lexical statement is cancelled after `SPARSE_STATEMENT_TIMEOUT_MS` (that retriever is then reported as degraded). A query with no distinctive word contributes no lexical
candidates; the dense retriever still answers.

**Partial failure.** If one retriever fails (the embedding model is unavailable, or the lexical query fails) the other
still answers and `degraded` names the one that failed; `retrievers_used` lists those that did. If both fail the
response is 503. Documents that are not `ready` are never searched, and only the caller's own documents are searched,
administrators included.

**Errors**: 401, 404 (foreign or missing filter), 413 (body over 128 KiB), 422 (invalid query, `top_k` or filters), 429
(rate limit), 503 (no retriever available).

#### Optional reranking (Phase 8)

Add `"rerank": true` (and optionally `"rerank_candidates": 10`) to re-score the best hybrid candidates with a
cross-encoder. Off by default: without the flag the request, response and order are exactly as above, and
`POST /search/semantic` never reranks. The server must have `RERANK_ENABLED=true`, otherwise a request with
`rerank: true` is a `422 RERANKING_DISABLED`.

- **Candidates.** The first `rerank_candidates` results of the (owner-scoped, `ready`-only) hybrid ranking, at most
  `RERANK_MAX_CANDIDATES`, default `RERANK_CANDIDATES` (10). Callers cannot name chunks. `top_k` must not exceed the
  candidate count (422); when omitted it is `min(SEARCH_DEFAULT_TOP_K, candidates)`.
- **Response.** The shape above plus, per result, `retrieval_rank` (position in the hybrid ranking), `rerank_score`
  and `rerank_truncated`, and `search.rerank` (`applied`, `fallback_reason`, `score_type: "raw_logit"`, `model`,
  `model_revision`, `device`, `candidates`, `truncated_candidates`, `latency_ms`). `rank` is the final position;
  `score`, `dense_rank`, `sparse_rank` and `dense_score` keep their retrieval values.
- **Score semantics.** `rerank_score` is the model's raw output (a logit): higher means more relevant to this query.
  It is not a probability, is not normalised, is not comparable between queries or models, and is never stored.
  Results are ordered by it; ties fall back to the retrieval rank.
- **Long passages.** A query and passage are scored together up to `RERANK_MAX_LENGTH` tokens (512); a longer passage
  loses its end, and `rerank_truncated` is true for it.
- **Failure.** If the model is unavailable, busy, too slow, fails or returns unusable scores, the response is still
  200 with the hybrid order, `rerank.applied: false`, a `fallback_reason` (`unavailable`, `busy`, `timeout`, `failed`,
  `invalid`), `rerank_score: null`, and `rerank` added to `degraded`. A reranked-looking list is never returned
  without reranking having happened.

---

## RAG Endpoints

### POST /rag/query

Query documents using RAG.

**Authentication**: Required

**Request Body**:
```json
{
  "question": "What are the main types of machine learning?",
  "conversation_id": "uuid",
  "retrieval_config": {
    "top_k": 10,
    "min_similarity": 0.7,
    "rerank": true
  },
  "generation_config": {
    "provider": "openai",
    "model": "gpt-4",
    "temperature": 0.7,
    "max_tokens": 1000
  }
}
```

**Response** (200):
```json
{
  "data": {
    "answer": "Based on the documents, there are three main types of machine learning: supervised learning, unsupervised learning, and reinforcement learning...",
    "citations": [
      {
        "chunk_id": "uuid",
        "document_id": "uuid",
        "document_name": "ml_overview.pdf",
        "page_number": 5,
        "citation_text": "Machine learning can be categorized into three main types...",
        "confidence": 0.92
      }
    ],
    "conversation_id": "uuid",
    "message_id": "uuid",
    "retrieval_time_ms": 125,
    "generation_time_ms": 1500,
    "total_time_ms": 1625
  },
  "meta": {
    "request_id": "uuid",
    "timestamp": "2026-10-07T20:26:49.575Z"
  }
}
```

**Errors**:
- 400: No relevant documents found
- 500: LLM provider error

---

## Conversation Endpoints

### GET /conversations

List user's conversations.

**Authentication**: Required

**Query Parameters**:
- `page` (int, default: 1): Page number
- `page_size` (int, default: 20): Items per page

**Response** (200):
```json
{
  "data": [
    {
      "id": "uuid",
      "title": "ML Discussion",
      "message_count": 5,
      "created_at": "2026-10-07T20:26:49.575Z",
      "updated_at": "2026-10-07T20:26:49.575Z"
    }
  ],
  "meta": {
    "page": 1,
    "page_size": 20,
    "total_items": 10,
    "total_pages": 1,
    "request_id": "uuid",
    "timestamp": "2026-10-07T20:26:49.575Z"
  }
}
```

---

### POST /conversations

Create a new conversation.

**Authentication**: Required

**Request Body**:
```json
{
  "title": "ML Discussion"
}
```

**Response** (201):
```json
{
  "data": {
    "id": "uuid",
    "title": "ML Discussion",
    "message_count": 0,
    "created_at": "2026-10-07T20:26:49.575Z",
    "updated_at": "2026-10-07T20:26:49.575Z"
  },
  "meta": {
    "request_id": "uuid",
    "timestamp": "2026-10-07T20:26:49.575Z"
  }
}
```

---

### GET /conversations/{id}

Get conversation with messages.

**Authentication**: Required

**Path Parameters**:
- `id` (uuid): Conversation ID

**Query Parameters**:
- `limit` (int, default: 50): Number of messages

**Response** (200):
```json
{
  "data": {
    "id": "uuid",
    "title": "ML Discussion",
    "messages": [
      {
        "id": "uuid",
        "role": "user",
        "content": "What is machine learning?",
        "created_at": "2026-10-07T20:26:49.575Z"
      },
      {
        "id": "uuid",
        "role": "assistant",
        "content": "Machine learning is...",
        "citations": [
          {
            "document_name": "ml_overview.pdf",
            "page_number": 1
          }
        ],
        "created_at": "2026-10-07T20:26:49.575Z"
      }
    ],
    "created_at": "2026-10-07T20:26:49.575Z",
    "updated_at": "2026-10-07T20:26:49.575Z"
  },
  "meta": {
    "request_id": "uuid",
    "timestamp": "2026-10-07T20:26:49.575Z"
  }
}
```

**Errors**:
- 404: Conversation not found
- 403: Not authorized

---

### DELETE /conversations/{id}

Delete conversation.

**Authentication**: Required

**Path Parameters**:
- `id` (uuid): Conversation ID

**Response** (204): No content

**Errors**:
- 404: Conversation not found
- 403: Not authorized

---

## Evaluation Endpoints

### POST /evaluations

Create and run evaluation.

**Authentication**: Required

**Request Body**:
```json
{
  "name": "Retrieval Quality Test",
  "config": {
    "evaluation_type": "retrieval",
    "metrics": ["recall_at_k", "mrr", "hit_at_k"],
    "k_values": [5, 10, 20],
    "dataset": "benchmark_v1"
  }
}
```

**Response** (201):
```json
{
  "data": {
    "id": "uuid",
    "name": "Retrieval Quality Test",
    "status": "pending",
    "created_at": "2026-10-07T20:26:49.575Z"
  },
  "meta": {
    "request_id": "uuid",
    "timestamp": "2026-10-07T20:26:49.575Z"
  }
}
```

---

### GET /evaluations/{id}

Get evaluation results.

**Authentication**: Required

**Path Parameters**:
- `id` (uuid): Evaluation run ID

**Response** (200):
```json
{
  "data": {
    "id": "uuid",
    "name": "Retrieval Quality Test",
    "status": "completed",
    "results_summary": {
      "recall_at_5": 0.85,
      "recall_at_10": 0.92,
      "mrr": 0.78,
      "hit_at_5": 0.88
    },
    "results": [
      {
        "metric_name": "recall_at_5",
        "metric_value": 0.85,
        "details": {}
      }
    ],
    "started_at": "2026-10-07T20:26:49.575Z",
    "completed_at": "2026-10-07T20:26:49.575Z",
    "created_at": "2026-10-07T20:26:49.575Z"
  },
  "meta": {
    "request_id": "uuid",
    "timestamp": "2026-10-07T20:26:49.575Z"
  }
}
```

**Errors**:
- 404: Evaluation not found
- 403: Not authorized

---

### GET /evaluations

List evaluation runs.

**Authentication**: Required

**Query Parameters**:
- `page` (int, default: 1): Page number
- `page_size` (int, default: 20): Items per page
- `status` (string, optional): Filter by status

**Response** (200):
```json
{
  "data": [
    {
      "id": "uuid",
      "name": "Retrieval Quality Test",
      "status": "completed",
      "created_at": "2026-10-07T20:26:49.575Z"
    }
  ],
  "meta": {
    "page": 1,
    "page_size": 20,
    "total_items": 5,
    "total_pages": 1,
    "request_id": "uuid",
    "timestamp": "2026-10-07T20:26:49.575Z"
  }
}
```

---

## Health Endpoints

### GET /health

Check system health.

**Authentication**: None

**Response** (200):
```json
{
  "status": "healthy",
  "services": {
    "database": "healthy",
    "redis": "healthy",
    "workers": "healthy"
  },
  "timestamp": "2026-10-07T20:26:49.575Z"
}
```

**Response** (503):
```json
{
  "status": "unhealthy",
  "services": {
    "database": "unhealthy",
    "redis": "healthy",
    "workers": "healthy"
  },
  "timestamp": "2026-10-07T20:26:49.575Z"
}
```

---

## Rate Limiting

**Default Limits**:
- API endpoints: 100 requests per minute per user
- Authentication endpoints: 10 requests per minute per IP
- Upload endpoint: 20 uploads per minute per user
- Semantic search: 60 searches per minute per user (`RATE_LIMIT_SEARCH_ATTEMPTS`); every search costs a model inference

**Headers**:
```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 95
X-RateLimit-Reset: 1602083200
```

**Error Response** (429):
```json
{
  "error": {
    "code": "RATE_LIMIT_EXCEEDED",
    "message": "Rate limit exceeded. Please try again later.",
    "details": {
      "retry_after": 60
    }
  }
}
```

---

## Error Codes

| Code | HTTP Status | Description |
|------|-------------|-------------|
| VALIDATION_ERROR | 422 | Request validation failed |
| AUTHENTICATION_ERROR | 401 | Invalid or missing token |
| REQUEST_TOO_LARGE | 413 | Request body over the endpoint's limit (search: 128 KiB) |
| EMBEDDING_UNAVAILABLE | 503 | Semantic search cannot run right now (model unavailable, timed out or busy) |
| AUTHORIZATION_ERROR | 403 | Insufficient permissions |
| NOT_FOUND | 404 | Resource not found |
| CONFLICT | 409 | Resource conflict |
| RATE_LIMIT_EXCEEDED | 429 | Rate limit exceeded |
| FILE_TOO_LARGE | 413 | File exceeds size limit |
| UNSUPPORTED_FILE_TYPE | 415 | File type not supported |
| PROCESSING_ERROR | 500 | Document processing failed |
| LLM_ERROR | 500 | LLM provider error |
| INTERNAL_ERROR | 500 | Internal server error |

---

*This API specification will be implemented across Phase 1-16. Each endpoint's implementation will include proper error handling, validation, and authorization.*

## Answer Generation (Phase 9)

### POST /rag/answer

Answers a question from the caller's own searchable documents, with citations that point at the sources the language
model was actually shown. Off by default (`RAG_ENABLED=false`): then every call is `503 RAG_DISABLED` and no provider is
created. Nothing is stored (no conversation history).

**Authentication**: Required. Rate limited per user with its own budget (`RATE_LIMIT_RAG_ATTEMPTS`, default 10 per
window); a disabled endpoint does not spend it. The body is capped at 128 KiB.

**Request Body**:
```json
{
  "question": "When are Aman rice seedlings transplanted?",
  "document_ids": ["uuid1"],
  "collection_id": "uuid2",
  "retrieval": "hybrid",
  "rerank": false,
  "max_sources": 5,
  "max_answer_tokens": 300
}
```

Only `question` is required (at most `SEARCH_MAX_QUERY_CHARS` characters). `retrieval` is `hybrid` or `semantic`
(server default `RAG_RETRIEVAL_MODE`). `rerank: true` re-scores the candidates with the Phase 8 cross-encoder and needs
`RERANK_ENABLED=true` (otherwise `422 RERANKING_DISABLED`); if reranking fails, retrieval order is used and `rerank` appears
in `retrieval.degraded`. `max_sources` and `max_answer_tokens` may only lower the server limits (`RAG_MAX_SOURCES`,
`LLM_MAX_OUTPUT_TOKENS`); asking for more is a 422. Filters that name other users' documents or collections are the same
404 as missing ones, before any model is called.

**Response** (200):
```json
{
  "data": {
    "answer": "Farmers transplant Aman rice seedlings in July [S1].",
    "insufficient_evidence": false,
    "insufficient_reason": null,
    "citations": [
      {
        "label": "S1",
        "document_id": "uuid",
        "chunk_id": "uuid",
        "document_name": "rice.txt",
        "chunk_index": 0,
        "section_ordinal": 0,
        "page_number": null,
        "heading": "Planting",
        "heading_path": ["Monsoon Rice Farming", "Planting"],
        "start_char": 0,
        "end_char": 172,
        "excerpt": "Farmers transplant Aman rice seedlings in July, when monsoon rain has filled the fields...",
        "excerpt_truncated": true,
        "source_truncated": false
      }
    ],
    "retrieval": {
      "mode": "hybrid", "candidates": 10, "dense_candidates": 10, "sparse_candidates": 7,
      "retrievers_used": ["dense", "sparse"], "degraded": [], "reranked": false,
      "embedding_model": "intfloat/multilingual-e5-small", "model_revision": "614241f6..."
    },
    "context": {
      "sources_supplied": 5, "duplicates_dropped": 0, "truncated_sources": 0,
      "estimated_input_tokens": 700, "budget_tokens": 2400
    },
    "generation": {
      "provider": "ollama", "model": "qwen3.5:9b", "prompt_version": "answer_v1", "attempts": 1,
      "input_tokens": 790, "output_tokens": 38, "finish_reason": "stop", "latency_ms": 14200
    },
    "validation": {
      "unknown_labels_removed": 0, "uncited_sentences_removed": 0, "fabricated_quotes_removed": 0,
      "unsupported_literals_removed": 0, "citation_granularity": "sentence"
    },
    "timings": {"retrieval_ms": 31, "rerank_ms": 0, "context_ms": 0, "generation_ms": 14210, "total_ms": 14245}
  },
  "meta": {"request_id": "uuid", "timestamp": "2026-10-13T00:00:00Z"}
}
```

**Citation contract.** The model is shown the selected chunks as `S1`, `S2`, ... for this request only and must write
`[S1]`-style markers after every factual sentence. The application validates the output locally whatever the provider
promised: the JSON is parsed strictly; markers for labels that were not supplied are removed and never become
citations; a sentence with no valid label is dropped; a sentence with a double-quoted passage that is not in one of its
cited sources, or with a number or identifier (any script's digits) that is not in them as a whole token, is dropped.
Each citation object is built from the database record of the chunk behind the label (document id, chunk id, document
name, page for formats that have pages and `null` otherwise, section, heading, offsets) plus a bounded `excerpt` of the
text the model saw; nothing in it comes from the model's own words. `source_truncated` is true when the chunk was longer
than `RAG_MAX_CHUNK_TOKENS` and its end was not shown. If a model lists `citations` but writes no inline markers at all,
the listed (valid) labels apply to the whole answer and `validation.citation_granularity` is `answer` instead of
`sentence`.

**Insufficient evidence.** When nothing was retrieved (`no_sources`; the model is not called), when retrieval was degraded
and found nothing (`retrieval_degraded`), when the model says the sources do not answer the question (`model_abstained`), or
when every sentence of its answer failed validation (`no_supported_sentences`), the response is 200 with
`insufficient_evidence: true`, an empty `citations` list and a fixed message in the language of the question. The model's
own unvalidated text is never returned.

**Failures** (never an invented answer): `503 GENERATION_UNAVAILABLE` with `details.reason` `timeout`, `unavailable`,
`rate_limited` (plus `Retry-After` when the provider gave one) or `busy` (all generation slots taken for
`RAG_QUEUE_TIMEOUT_SECONDS`); `502 GENERATION_FAILED` when the provider rejected the request, answered with something that
is not a usable answer object twice in a row, or reported a prompt that filled its context window; `503 RAG_DISABLED`; plus the
usual 401, 404, 413, 422 and 429.

**Not returned**: the prompt, the system instructions, the provider URL or key, raw model output, other users' data.
