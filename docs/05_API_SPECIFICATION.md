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

Document: `{"id", "filename", "file_type", "content_type", "file_size", "checksum_sha256", "status", "error_message", "collections": [{"id","name"}], "created_at", "updated_at"}`

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

**Response** (201): the Document with `"status": "pending"`.

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

Semantic search using vector similarity.

**Authentication**: Required

**Request Body**:
```json
{
  "query": "machine learning algorithms",
  "top_k": 10,
  "min_similarity": 0.7,
  "filters": {
    "document_ids": ["uuid1", "uuid2"],
    "collection_ids": ["uuid3"],
    "file_types": ["pdf"]
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
        "similarity_score": 0.89,
        "metadata": {}
      }
    ],
    "total_results": 10,
    "query_time_ms": 45
  },
  "meta": {
    "request_id": "uuid",
    "timestamp": "2026-10-07T20:26:49.575Z"
  }
}
```

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

Hybrid search combining semantic and keyword.

**Authentication**: Required

**Request Body**:
```json
{
  "query": "machine learning algorithms",
  "top_k": 10,
  "semantic_weight": 0.6,
  "keyword_weight": 0.4,
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
        "semantic_score": 0.89,
        "keyword_score": 0.45,
        "combined_score": 0.73,
        "metadata": {}
      }
    ],
    "total_results": 10,
    "query_time_ms": 58
  },
  "meta": {
    "request_id": "uuid",
    "timestamp": "2026-10-07T20:26:49.575Z"
  }
}
```

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
