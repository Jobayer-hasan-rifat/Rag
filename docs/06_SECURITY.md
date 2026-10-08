# Security

## Overview

This document defines the comprehensive security requirements, controls, and practices for the Intelligent Document Processing & RAG Platform.

## Security Architecture

### Defense in Depth

The platform implements security at multiple layers:

```mermaid
graph TB
    Layer1[Network Layer]
    Layer2[Application Layer]
    Layer3[Data Layer]
    Layer4[Process Layer]
    
    Layer1 --> HTTPS[HTTPS/TLS]
    Layer1 --> CORS[CORS Policy]
    Layer1 --> RateLimit[Rate Limiting]
    
    Layer2 --> AuthN[Authentication]
    Layer2 --> AuthZ[Authorization]
    Layer2 --> Input[Input Validation]
    Layer2 --> Output[Output Sanitization]
    
    Layer3 --> Encryption[Encryption at Rest - Future]
    Layer3 --> Access[Access Control]
    Layer3 --> Secrets[Secrets Management]
    
    Layer4 --> File[File Handling]
    Layer4 --> Process[Process Isolation]
    Layer4 --> Audit[Audit Logging]
    
    style Layer1 fill:#e1f5ff
    style Layer2 fill:#fff4e1
    style Layer3 fill:#ffe1f5
    style Layer4 fill:#e1ffe1
```

## Authentication Security

### Password Security

**Algorithm**: bcrypt via the `bcrypt` library. The work factor is configurable
(`BCRYPT_COST_FACTOR`, default 12) and must be at least 12 in staging and production.
Argon2id is a reasonable future alternative; bcrypt is the project standard (DEC-017).

**Policy** (enforced by the registration schema): 8-72 bytes (bcrypt ignores input beyond 72
bytes, so longer passwords are rejected rather than silently truncated), at least one
uppercase letter, one lowercase letter and one digit. A breached/common-password check is a
future enhancement.

**Handling**:
- Hashing and verification run in a worker thread so they do not block the event loop.
- Login always performs one bcrypt comparison, using a dummy hash for unknown accounts, so
  response time does not reveal whether an account exists.
- Passwords and hashes are never returned by the API, never logged, and never echoed in validation errors.

### Access Tokens (JWT)

- Signed with HMAC (`HS256` by default; `HS384`/`HS512` allowed) using `JWT_SECRET_KEY` (at
  least 32 characters; placeholder values are rejected outside development). The accepted
  algorithm is pinned to the configured one, so `none` and algorithm-confusion tokens fail.
- Lifetime 15 minutes (`ACCESS_TOKEN_EXPIRE_MINUTES`).
- Claims: `sub` (user id), `jti`, `type` (`access`), `iss`, `aud`, `iat`, `exp`. All are required
  and validated. No email, role, password or other personal data is placed in the token.
- The user's **role and active status are read from the database on every request**, never from the token, so
  deactivation and role changes take effect immediately (DEC-013).
- Logout adds the token's `jti` to a Redis denylist until its natural expiry. If the denylist is
  unreachable, authenticated requests fail closed with 503.

### Refresh Tokens

- Opaque 384-bit random values (not JWTs). Only a SHA-256 digest is stored, so a database leak
  does not yield usable tokens.
- Single use with rotation: every refresh revokes the presented token and issues a new one in the same *family* (one family per login).
- **Reuse detection**: presenting a token that was already rotated or revoked revokes the whole family, ending
  that session for both the thief and the victim, and logs a warning.
- Lifetime 7 days (`REFRESH_TOKEN_EXPIRE_DAYS`). Rows are locked (`SELECT ... FOR UPDATE`) while
  rotating, so concurrent use of one token cannot yield two valid successors.

### Session Management

- Multiple concurrent sessions per user are allowed (one family each); logout ends one session.
- Deactivating a user invalidates access immediately and revokes the family on next refresh.
- Planned: revoke all sessions on password change; scheduled cleanup of expired rows.

### Brute Force and Enumeration

- Login, registration and refresh are rate limited in Redis (see Rate Limiting).
- Login failures return one generic 401 regardless of cause.
- Registration returns a neutral 409 for an existing email. This still reveals existence; it is
  mitigated by rate limiting and accepted until email verification exists.

## Authorization Security

### Role-Based Access Control (RBAC)

**Roles**:
- `user`: Standard user with basic permissions
- `admin`: Administrator with full permissions

**Permissions**:
```python
PERMISSIONS = {
    "user": [
        "documents:read:own",
        "documents:write:own",
        "documents:delete:own",
        "collections:read:own",
        "collections:write:own",
        "collections:delete:own",
        "conversations:read:own",
        "conversations:write:own",
        "conversations:delete:own",
        "search:query",
        "rag:query",
        "evaluations:read:own",
        "evaluations:write:own",
    ],
    "admin": [
        "documents:read:all",
        "documents:write:all",
        "documents:delete:all",
        "users:read:all",
        "users:write:all",
        "users:delete:all",
        "evaluations:read:all",
        "evaluations:write:all",
        # ... all user permissions plus admin
    ]
}
```

### Resource Access Control

**Principle**: Users can only access their own resources unless explicitly shared.

**Implementation**:
```python
# Every query filters by user_id
async def get_document(document_id: UUID, user: User) -> Document:
    document = await document_repo.get(document_id)
    if document.user_id != user.id and user.role != "admin":
        raise AuthorizationError("Not authorized to access this document")
    return document
```

**In Database**:
- All queries include `WHERE user_id = current_user_id`
- Foreign key constraints prevent cross-user references
- Indexes on user_id for query performance

## File Handling Security

All uploaded files, and every part of an upload (name, declared type, content), are untrusted. Phase 3 never
parses, renders or executes document contents.

### Upload Pipeline

1. **Authenticate and rate limit first.** These run as dependencies before the multipart body is read, so unauthenticated or throttled clients cost almost nothing.
2. **Cap the body.** `BodySizeLimitMiddleware` rejects a declared `Content-Length` above the limit immediately and counts streamed bytes for requests without one, before multipart parsing spools data to disk. Exactly one file and at most one other field are accepted.
3. **Validate** (`app/security/file_validation.py`):
   - the sanitised filename must have an allowed extension (`.pdf`, `.docx`, `.txt`, `.md`);
   - the declared `Content-Type` must not contradict the extension (a missing or `application/octet-stream` type is tolerated because clients differ; the content check is authoritative);
   - the content must match the type: PDF needs a `%PDF-` header in the first 1 KiB and `%%EOF` in the last 4 KiB; DOCX must be a ZIP container with `[Content_Types].xml` and `word/document.xml`, at most 5000 entries, a total uncompressed size under 1 GiB and a compression ratio under 200:1; TXT/MD must be valid UTF-8 without NUL bytes;
   - size is enforced while reading (`MAX_UPLOAD_BYTES`, default 50 MiB) and empty files are rejected.
4. **Checksum, deduplicate, check quota**: SHA-256 is computed in the same pass; identical content from the same user is refused (409) before anything is stored, and an upload that would push the user past their storage quota (`MAX_STORAGE_BYTES_PER_USER`, default 1 GiB) is refused with 403.
5. **Store** the file by streaming it in 64 KiB chunks under a generated key, then **re-verify** that the stored size and hash match.
6. **Persist** the record. If that fails, the stored file is deleted. The user can never see a record without a file from a failed upload.

**Limitations of type detection**: signatures and container structure prove a file *looks like* its type, not that it is benign or well formed. A crafted file can satisfy these checks. This is why files are never opened by the API, are always served as attachments with `nosniff` and a sandboxing CSP, and why parsing (Phase 4) must happen in isolated workers with limits. Legacy `.doc`, UTF-16 text and other formats are rejected rather than guessed at.

### Filenames

The client filename is display metadata only. It is NFC-normalised, reduced to its last path component (both `/` and `\` are separators), stripped of control, format and bidi-override characters, whitespace-collapsed, trimmed of leading/trailing dots and spaces, and truncated to 255 characters keeping its extension. It never influences where a file is stored, and downloads use `Content-Disposition` with an ASCII fallback and a percent-encoded UTF-8 name, so quotes, CR/LF and non-ASCII cannot inject headers.

### File Storage Security

- Storage is behind `StorageProvider`; the local backend writes under `STORAGE_LOCAL_PATH` (a private directory, absolute in staging/production, never served by the application: there are no static mounts).
- Keys are generated with a CSPRNG (`documents/{2 hex}/{32 hex}`) and independent of user, filename and content. The local backend accepts only that exact format, resolves the path and verifies it is inside the root.
- Writes go to a temporary file in the root, are made owner-only (`0600`, directories `0700`), then are atomically hard-linked into place, so an existing object is never overwritten and a failed write leaves nothing behind.
- Files are never executable and are never executed.

### Authorization for Files

Every endpoint resolves the document through one service method that returns 404 for both "does not exist" and "belongs to someone else", so identifiers cannot be probed. Database foreign keys additionally guarantee that a document and a collection linked together share an owner. Administrators can read, download, rename and delete any document by ID per the documented RBAC policy; listing and uploading act only on the caller's own data.

### Deletion and Consistency

Downloads also compare the stored size with the recorded size and fail closed on a mismatch. Deletion removes the stored object first, then the record: a storage failure keeps the record so the caller can retry, and a record never claims a file that was successfully removed. A missing file during deletion is logged at ERROR and deletion proceeds; a missing file during download returns 500 `STORAGE_INCONSISTENCY` and is logged for operators. An upload whose record cannot be saved removes its file; if even that cleanup fails, the orphaned key is logged at ERROR (see the audit for the planned reconciliation job).

### Logging

Filenames and file contents are never logged; events carry user and document IDs. A storage key is logged only when an object could not be cleaned up, so operators can find the orphan (the key is an opaque random identifier).

### Not Yet Implemented

Antivirus scanning, parsing isolation (Phase 4), a reconciliation job for orphaned objects, and encryption at rest (provided by the storage layer in production). The per-user quota is checked before each upload but is not atomic across simultaneous uploads, so it can be overshot by a few files.

## Document Processing Security

Extracted text is **data**. It is stored and later retrieved, never executed, rendered or interpreted as instructions. This matters most for the RAG
phases, where text inside a document may try to steer the model (prompt injection); processing neither strips nor obeys such text.

**Parser containment**
- Parsing happens only in Celery workers, never in the API process. PyMuPDF does not run JavaScript, launch actions or fetch external resources; pages are never rendered. python-docx does not resolve external XML entities, macros and embedded objects are ignored, and DOCX archives are never extracted to disk.
- The worker container runs as a non-root user with a read-only root filesystem (only a tmpfs `/tmp` and the upload volume are writable), all Linux capabilities dropped, `no-new-privileges`, a memory cap (`WORKER_MEMORY_LIMIT`, default 2 GiB) and a process cap. A parser crash or out-of-memory kill takes down one worker process; the task is redelivered and the document's attempt budget stops a repeat offender.
- Chunking is a pure function over already-validated, normalised text and is treated as an attack surface: it runs only in workers, honours the per-document deadline, is linear-time on adversarial input (long runs of punctuation, combining marks, whitespace or no whitespace; regression-tested), and a document producing more than `CHUNKING_MAX_CHUNKS` chunks fails safely with `too_many_chunks`. Chunk text is document content, so it is returned only by `GET /documents/{id}/chunks` to the owner (404 for others, identical to a missing document), is never logged, and is deleted with the document by cascade.
- Parser libraries are version-pinned and covered by `pip-audit`. Running parsers in a separate, network-less sandbox container is a recommended future hardening.

**Resource limits** (all configurable, see `.env.example`): file size (`MAX_UPLOAD_BYTES`), pages (`PROCESSING_MAX_PAGES`, default 2000), extracted characters (`PROCESSING_MAX_TEXT_CHARS`, default 20 million), DOCX archive size, entry count and compression ratio
(`PROCESSING_MAX_DOCX_UNCOMPRESSED_BYTES`), a cooperative time budget per document (`PROCESSING_TIMEOUT_SECONDS`, default 120 s) with Celery soft (+15 s) and hard (+45 s) kill limits, worker recycling by task count and memory, and a bounded retry budget.

**Task flooding**: uploads and retries are rate limited per user, uploads are bounded by the storage quota, the recovery sweep re-queues at most 100 documents per run and only documents idle for two minutes, and duplicate queue messages are harmless because a document can only be claimed once.

**Authorization**: workers receive only a document ID. The retry endpoint resolves the document through the same owner-or-admin check as every other endpoint (404 otherwise).

**Data hygiene**: failure details shown to users are fixed strings; logs carry IDs, counts, durations and failure codes but never document text, filenames or file-embedded metadata; file properties (title, author) are untrusted, normalised, truncated and stored only in `processing_metadata`.

**Known limits**: scanned/image-only PDFs yield no text (reported as `empty_document`; OCR is out of scope); PDFs using legacy non-Unicode Bangla fonts can extract as garbled text, which cannot be detected reliably; multi-column layouts and repeated headers/footers are not reconstructed or removed.

## Input Validation Security

### Pydantic Validation

All input validated with Pydantic schemas:

`RegisterRequest` (`app/schemas/auth.py`) normalises the email, enforces the password policy and rejects
unknown fields (`extra="forbid"`), which blocks mass assignment such as `{"role": "admin"}`. Validation messages
state the rule that failed and never include the submitted value.

### SQL Injection Prevention

**Using SQLAlchemy ORM**:
```python
# SAFE: SQLAlchemy parameterizes queries
documents = await db.execute(
    select(Document).where(Document.user_id == user_id)
)

# SAFE: Explicit parameterization
documents = await db.execute(
    text("SELECT * FROM documents WHERE user_id = :user_id"),
    {"user_id": user_id}
)

# DANGEROUS: String concatenation (NEVER DO THIS)
# documents = await db.execute(
#     text(f"SELECT * FROM documents WHERE user_id = {user_id}")
# )
```

### XSS Prevention

**Backend**:
- JSON API (no HTML rendering)
- No user content in server-rendered pages

**Frontend**:
- React auto-escapes by default
- Never use `dangerouslySetInnerHTML` with user content
- Content Security Policy (CSP) headers

### Path Traversal Prevention

```python
# Validate and sanitize all file paths
def validate_path(user_path: str) -> str:
    # Remove null bytes
    path = user_path.replace('\x00', '')
    
    # Normalize path
    path = os.path.normpath(path)
    
    # Check for traversal patterns
    if '..' in path or path.startswith('/'):
        raise SecurityError("Invalid path")
    
    return path
```

## Secrets Management

### Environment Variables

**All secrets via environment variables**:

```bash
# .env.example (committed to repo)
DATABASE_URL=postgresql+asyncpg://user:pass@localhost/db
REDIS_URL=redis://localhost:6379/0
SECRET_KEY=your-secret-key-change-in-production
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...

# .env (NOT committed)
DATABASE_URL=postgresql+asyncpg://prod_user:strong_password@prod-host/db
SECRET_KEY=very-long-random-string-generated-securely
OPENAI_API_KEY=sk-prod-...
```

### Configuration Validation

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str
    redis_url: str
    secret_key: str
    openai_api_key: str | None = None
    
    @validator('secret_key')
    def validate_secret_key(cls, v):
        if len(v) < 32:
            raise ValueError('SECRET_KEY must be at least 32 characters')
        return v
    
    class Config:
        env_file = '.env'
        case_sensitive = False

settings = Settings()
```

### Production Secrets

**Development**: `.env` file (git-ignored)

**Production** (Future):
- AWS Secrets Manager
- HashiCorp Vault
- Kubernetes Secrets
- Environment variables in deployment

**Rotation Policy**:
- Rotate secrets on compromise
- Rotate periodically (every 90 days for critical secrets)
- Document rotation procedure

## Logging Security

### What to Log

✅ **Log These**:
- Authentication attempts (success and failure)
- Authorization failures
- File uploads (metadata, not content)
- Processing errors
- API errors
- Security events

### What NOT to Log

❌ **Never Log These**:
- Passwords (even hashed)
- JWT tokens
- API keys
- Credit card numbers
- SSNs
- Private keys
- Document content (unless necessary for debugging)

### Secure Logging Implementation

```python
import logging
import json

class SecureFormatter(logging.Formatter):
    SENSITIVE_FIELDS = ['password', 'token', 'api_key', 'secret']
    
    def format(self, record):
        # Convert to dict
        log_data = {
            'timestamp': self.formatTime(record),
            'level': record.levelname,
            'message': record.getMessage(),
            'request_id': getattr(record, 'request_id', None),
        }
        
        # Sanitize sensitive data
        for field in self.SENSITIVE_FIELDS:
            if field in str(log_data):
                log_data['sanitized'] = f'{field} removed for security'
                del log_data[field]  # This is illustrative; actual sanitization is more complex
        
        return json.dumps(log_data)
```

## Rate Limiting

### Implementation

Authentication endpoints use a fixed-window counter in Redis (`app/security/rate_limit.py`):

| Scope | Key | Budget (default) |
|-------|-----|------------------|
| `register:ip`, `login:ip`, `refresh:ip` | client IP | 10 per 60 s |
| `login:email` | SHA-256 of the email | 5 x the IP budget per 60 s |

- The per-account budget is larger than the per-IP budget so that a single address cannot
  exhaust it and lock a victim out of their own account; distributed guessing against one
  account is still capped.
- Exceeding a budget returns 429 `RATE_LIMIT_EXCEEDED` with a `Retry-After` header.
- Identifiers are hashed in Redis keys. The client IP is the direct TCP peer; behind a reverse proxy, the
  proxy must be configured as a trusted source of forwarded headers (to be configured at deployment).
- If Redis is unavailable the limiter fails open and logs an error (availability over throttling); the token denylist fails closed.
- Requires Redis 7+ (`EXPIRE ... NX`). Configure with `RATE_LIMIT_ENABLED`, `RATE_LIMIT_AUTH_ATTEMPTS`, `RATE_LIMIT_WINDOW_SECONDS`; it cannot be disabled in staging/production.

### Rate Limit Strategy

| Endpoint Type | Limit | Window |
|--------------|-------|--------|
| Authentication | 10 requests | 1 minute |
| Document Upload | 20 requests | 1 minute |
| Search | 100 requests | 1 minute |
| RAG Queries | 60 requests | 1 minute |
| General API | 100 requests | 1 minute |

### Response Headers

Throttled responses include `Retry-After` (seconds). `X-RateLimit-*` headers are not implemented.

## Security Headers

### HTTPS & TLS

**Development**: Not enforced (local HTTP)

**Production** (Future):
- TLS 1.3 required
- HSTS header
- Secure cookies

### HTTP Security Headers

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://app.example.com"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

# Security headers middleware
@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Content-Security-Policy"] = "default-src 'self'"
    return response
```

## Dependency Security

### Dependency Management

**Pin Exact Versions**:
```txt
# requirements.txt
fastapi==0.104.1
pydantic==2.5.0
sqlalchemy==2.0.23
# ... not fastapi>=0.104.1
```

**Vulnerability Scanning**:
```bash
# Install security scanner
pip install pip-audit

# Check for known vulnerabilities
pip-audit

# Check requirements file
pip-audit -r requirements.txt
```

### Security Audit Schedule

- **Automated**: On every CI build
- **Manual**: Monthly review of dependencies
- **Immediate**: On CVE disclosure for critical dependencies

## Security Review Checklist

### Before Every Commit

- [ ] No secrets in code
- [ ] No secrets in logs
- [ ] Input validation present
- [ ] Authorization checks present
- [ ] Error messages don't leak information

### Before Every Release

- [ ] Dependency vulnerability scan
- [ ] Code review for security issues
- [ ] Authorization tested
- [ ] File upload security tested
- [ ] Rate limiting working

### Security Testing

**Unit Tests**:
- Password hashing verification
- Token validation
- Authorization enforcement
- Input validation

**Integration Tests**:
- Authentication flows
- Authorization boundaries
- File upload validation
- Rate limiting

**Security Tests**:
- SQL injection attempts
- XSS attempts
- Path traversal attempts
- Authentication bypass attempts

## Incident Response

### Security Incident Plan

1. **Detection**: Monitoring alerts, user reports, vulnerability disclosure
2. **Containment**: Isolate affected systems, revoke compromised tokens
3. **Investigation**: Determine scope and cause
4. **Remediation**: Fix vulnerability, patch systems
5. **Recovery**: Restore services, rotate secrets
6. **Post-Mortem**: Document lessons learned

### Contact Information

**Maintainer**: MD. Jobayer Hasan
**Security Email**: jobayer9948@gmail.com

## Security Best Practices Summary

### Authentication
✅ Bcrypt password hashing (cost ≥12)
✅ Short-lived access tokens (15 min)
✅ Refresh token rotation
✅ Secure token storage (hashed in DB)

### Authorization
✅ RBAC with clear permissions
✅ Resource ownership enforcement
✅ No trusting client-provided user IDs

### File Handling
✅ Multi-layer validation
✅ Secure storage (outside web root)
✅ Path traversal prevention
✅ All files treated as untrusted

### Input Validation
✅ Pydantic schemas for all inputs
✅ SQL injection prevention via ORM
✅ XSS prevention (React auto-escape)
✅ Path traversal prevention

### Secrets Management
✅ Environment variables
✅ No secrets in code
✅ No secrets in logs
✅ Rotation procedure documented

### Logging
✅ Log security events
✅ Never log sensitive data
✅ Structured logging with request IDs

### Network Security
✅ HTTPS in production
✅ CORS configured
✅ Rate limiting implemented
✅ Security headers present

### Dependencies
✅ Pinned versions
✅ Vulnerability scanning
✅ Regular updates

---

*Security is an ongoing process, not a one-time checklist. This document will be updated as new threats emerge and best practices evolve.*
