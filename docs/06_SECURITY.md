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

**Requirements**:
- Minimum 8 characters
- At least 1 uppercase letter
- At least 1 lowercase letter
- At least 1 digit
- No common passwords (check against breach database - future)

**Implementation**:
```python
import bcrypt

# Hashing with bcrypt, cost factor 12
def hash_password(password: str) -> str:
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
    return hashed.decode('utf-8')

# Verification
def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.verify(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))
```

**Why bcrypt**:
- Built-in salt
- Adjustable cost factor (future-proof)
- Resistant to GPU/ASIC attacks
- Industry standard

### JWT Token Security

**Access Token**:
- Algorithm: HS256 or RS256
- Expiry: 15 minutes
- Contains: user_id, role, exp, iat, jti
- Stored: Client-side (memory or httpOnly cookie)

**Refresh Token**:
- Expiry: 7 days
- Stored: Database (hashed with SHA-256)
- One-time use (rotated on refresh)
- Revoked on logout

**Token Structure**:
```json
{
  "sub": "user_uuid",
  "role": "user",
  "exp": 1602083200,
  "iat": 1602082300,
  "jti": "unique_token_id"
}
```

**Security Measures**:
- Short expiry on access tokens limits exposure window
- Refresh tokens hashed before database storage
- Token rotation prevents replay attacks
- Revocation list for compromised tokens

### Session Management

**Requirements**:
- One active session per user (configurable)
- Session invalidation on password change
- Session invalidation on logout
- Automatic cleanup of expired sessions

**Implementation**:
- Refresh tokens stored in database with expiration
- Cleanup job runs daily to remove expired tokens
- On logout, mark refresh token as revoked
- On password change, revoke all refresh tokens for user

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

### File Upload Validation

**Multi-layer Validation**:

1. **Extension Check** (First line, easy to bypass):
```python
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}
if not filename.lower().endswith(tuple(ALLOWED_EXTENSIONS)):
    raise ValidationError("Invalid file type")
```

2. **Magic Bytes Check** (Verify actual file type):
```python
MAGIC_BYTES = {
    b"%PDF": "application/pdf",
    b"PK\x03\x04": "application/docx",  # DOCX is a ZIP file
}
```

3. **File Size Limit**:
```python
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
if file_size > MAX_FILE_SIZE:
    raise ValidationError("File too large")
```

4. **Content Validation**:
- Parse file structure
- Check for embedded scripts
- Validate document integrity

### File Storage Security

**Secure Storage**:
- Files stored outside web root
- Random UUID filenames (not user-provided)
- Original filename stored in database only
- File permissions: read/write by application only

**Path Traversal Prevention**:
```python
import os

def sanitize_path(base_dir: str, filename: str) -> str:
    # Resolve to absolute path
    full_path = os.path.realpath(os.path.join(base_dir, filename))
    
    # Ensure it's within base directory
    if not full_path.startswith(os.path.realpath(base_dir)):
        raise SecurityError("Path traversal attempt detected")
    
    return full_path
```

### Untrusted File Handling

**Assumption**: All uploaded files are potentially malicious.

**Measures**:
- Never execute uploaded files
- Parse in isolated workers (not API processes)
- Memory limits on parsing
- Timeout on parsing operations
- No execution of embedded scripts

**PDF-Specific Risks**:
- Embedded JavaScript
- Launch actions
- External references
- Malformed PDFs

**Mitigation**:
- Use PyMuPDF with security options
- Disable JavaScript execution
- Disable external resource loading
- Catch parsing errors gracefully

## Input Validation Security

### Pydantic Validation

All input validated with Pydantic schemas:

```python
from pydantic import BaseModel, EmailStr, Field, validator

class UserCreate(BaseModel):
    email: EmailStr
    username: str = Field(..., min_length=3, max_length=50, regex="^[a-zA-Z0-9_]+$")
    password: str = Field(..., min_length=8)
    
    @validator('password')
    def validate_password(cls, v):
        if not any(c.isupper() for c in v):
            raise ValueError('Must contain uppercase letter')
        if not any(c.islower() for c in v):
            raise ValueError('Must contain lowercase letter')
        if not any(c.isdigit() for c in v):
            raise ValueError('Must contain digit')
        return v
```

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

```python
from fastapi import FastAPI, Request, HTTPException
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
app = FastAPI()

# General API rate limit
@app.route("/api/")
@limiter.limit("100/minute")
async def api_endpoint(request: Request):
    pass

# Stricter rate limit for auth
@app.route("/auth/login")
@limiter.limit("10/minute")
async def login(request: Request):
    pass
```

### Rate Limit Strategy

| Endpoint Type | Limit | Window |
|--------------|-------|--------|
| Authentication | 10 requests | 1 minute |
| Document Upload | 20 requests | 1 minute |
| Search | 100 requests | 1 minute |
| RAG Queries | 60 requests | 1 minute |
| General API | 100 requests | 1 minute |

### Response Headers

```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 95
X-RateLimit-Reset: 1602083200
```

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
