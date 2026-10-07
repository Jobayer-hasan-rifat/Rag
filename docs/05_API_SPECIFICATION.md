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

### GET /collections

List user's collections.

**Authentication**: Required

**Query Parameters**:
- `page` (int, default: 1): Page number
- `page_size` (int, default: 20, max: 100): Items per page
- `search` (string, optional): Search by name

**Response** (200):
```json
{
  "data": [
    {
      "id": "uuid",
      "name": "Research Papers",
      "description": "Academic papers on AI",
      "document_count": 15,
      "created_at": "2026-10-07T20:26:49.575Z",
      "updated_at": "2026-10-07T20:26:49.575Z"
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

### POST /collections

Create a new collection.

**Authentication**: Required

**Request Body**:
```json
{
  "name": "Research Papers",
  "description": "Academic papers on AI"
}
```

**Response** (201):
```json
{
  "data": {
    "id": "uuid",
    "name": "Research Papers",
    "description": "Academic papers on AI",
    "document_count": 0,
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
- 422: Validation error

---

### GET /collections/{id}

Get collection details.

**Authentication**: Required

**Path Parameters**:
- `id` (uuid): Collection ID

**Response** (200):
```json
{
  "data": {
    "id": "uuid",
    "name": "Research Papers",
    "description": "Academic papers on AI",
    "document_count": 15,
    "documents": [
      {
        "id": "uuid",
        "filename": "paper.pdf",
        "status": "ready"
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
- 404: Collection not found
- 403: Not authorized to access collection

---

### PATCH /collections/{id}

Update collection.

**Authentication**: Required

**Path Parameters**:
- `id` (uuid): Collection ID

**Request Body**:
```json
{
  "name": "Updated Name",
  "description": "Updated description"
}
```

**Response** (200):
```json
{
  "data": {
    "id": "uuid",
    "name": "Updated Name",
    "description": "Updated description",
    "document_count": 15,
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
- 404: Collection not found
- 403: Not authorized
- 422: Validation error

---

### DELETE /collections/{id}

Delete collection.

**Authentication**: Required

**Path Parameters**:
- `id` (uuid): Collection ID

**Response** (204): No content

**Errors**:
- 404: Collection not found
- 403: Not authorized

---

### POST /collections/{id}/documents

Add documents to collection.

**Authentication**: Required

**Path Parameters**:
- `id` (uuid): Collection ID

**Request Body**:
```json
{
  "document_ids": ["uuid1", "uuid2", "uuid3"]
}
```

**Response** (200):
```json
{
  "data": {
    "added_count": 3,
    "already_exists_count": 0
  },
  "meta": {
    "request_id": "uuid",
    "timestamp": "2026-10-07T20:26:49.575Z"
  }
}
```

**Errors**:
- 404: Collection not found
- 403: Not authorized
- 422: Invalid document IDs

---

### DELETE /collections/{id}/documents/{document_id}

Remove document from collection.

**Authentication**: Required

**Path Parameters**:
- `id` (uuid): Collection ID
- `document_id` (uuid): Document ID

**Response** (204): No content

**Errors**:
- 404: Collection or document not found
- 403: Not authorized

---

## Document Endpoints

### GET /documents

List user's documents.

**Authentication**: Required

**Query Parameters**:
- `page` (int, default: 1): Page number
- `page_size` (int, default: 20, max: 100): Items per page
- `status` (string, optional): Filter by status
- `file_type` (string, optional): Filter by file type
- `collection_id` (uuid, optional): Filter by collection
- `search` (string, optional): Search by filename
- `sort` (string, default: "created_at"): Sort field
- `order` (string, default: "desc"): Sort order (asc, desc)

**Response** (200):
```json
{
  "data": [
    {
      "id": "uuid",
      "filename": "research_paper.pdf",
      "file_type": "pdf",
      "file_size": 1048576,
      "status": "ready",
      "page_count": 15,
      "word_count": 5000,
      "chunk_count": 25,
      "created_at": "2026-10-07T20:26:49.575Z",
      "updated_at": "2026-10-07T20:26:49.575Z"
    }
  ],
  "meta": {
    "page": 1,
    "page_size": 20,
    "total_items": 50,
    "total_pages": 3,
    "request_id": "uuid",
    "timestamp": "2026-10-07T20:26:49.575Z"
  }
}
```

---

### POST /documents

Upload a new document.

**Authentication**: Required

**Request**: `multipart/form-data`

**Fields**:
- `file` (file, required): Document file (PDF, DOCX, TXT, MD)
- `collection_ids` (string, optional): JSON array of collection UUIDs

**File Constraints**:
- Maximum size: 50MB
- Allowed types: pdf, docx, txt, md

**Response** (201):
```json
{
  "data": {
    "id": "uuid",
    "filename": "research_paper.pdf",
    "file_type": "pdf",
    "file_size": 1048576,
    "status": "pending",
    "created_at": "2026-10-07T20:26:49.575Z"
  },
  "meta": {
    "request_id": "uuid",
    "timestamp": "2026-10-07T20:26:49.575Z"
  }
}
```

**Errors**:
- 413: File too large
- 415: Unsupported file type
- 422: Validation error

---

### GET /documents/{id}

Get document details.

**Authentication**: Required

**Path Parameters**:
- `id` (uuid): Document ID

**Response** (200):
```json
{
  "data": {
    "id": "uuid",
    "filename": "research_paper.pdf",
    "file_type": "pdf",
    "file_size": 1048576,
    "status": "ready",
    "error_message": null,
    "page_count": 15,
    "word_count": 5000,
    "chunk_count": 25,
    "metadata": {
      "author": "John Doe",
      "created_date": "2026-01-15"
    },
    "collections": [
      {
        "id": "uuid",
        "name": "Research Papers"
      }
    ],
    "versions": [
      {
        "version_number": 1,
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
- 404: Document not found
- 403: Not authorized

---

### DELETE /documents/{id}

Delete document.

**Authentication**: Required

**Path Parameters**:
- `id` (uuid): Document ID

**Response** (204): No content

**Errors**:
- 404: Document not found
- 403: Not authorized

---

### POST /documents/{id}/versions

Upload a new version of a document.

**Authentication**: Required

**Path Parameters**:
- `id` (uuid): Document ID

**Request**: `multipart/form-data`

**Fields**:
- `file` (file, required): New version of document

**Response** (201):
```json
{
  "data": {
    "id": "uuid",
    "filename": "research_paper.pdf",
    "file_type": "pdf",
    "file_size": 2097152,
    "status": "pending",
    "version_number": 2,
    "created_at": "2026-10-07T20:26:49.575Z"
  },
  "meta": {
    "request_id": "uuid",
    "timestamp": "2026-10-07T20:26:49.575Z"
  }
}
```

**Errors**:
- 404: Document not found
- 403: Not authorized
- 413: File too large
- 415: Unsupported file type

---

### GET /documents/{id}/versions

List document versions.

**Authentication**: Required

**Path Parameters**:
- `id` (uuid): Document ID

**Response** (200):
```json
{
  "data": [
    {
      "id": "uuid",
      "version_number": 2,
      "file_size": 2097152,
      "created_at": "2026-10-07T20:26:49.575Z"
    },
    {
      "id": "uuid",
      "version_number": 1,
      "file_size": 1048576,
      "created_at": "2026-10-07T20:26:49.575Z"
    }
  ],
  "meta": {
    "request_id": "uuid",
    "timestamp": "2026-10-07T20:26:49.575Z"
  }
}
```

**Errors**:
- 404: Document not found
- 403: Not authorized

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
