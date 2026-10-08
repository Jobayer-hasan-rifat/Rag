# Architecture

## Overview

This document describes the complete architecture of the Intelligent Document Processing & RAG Platform. The system follows a **Modular Monolith + Asynchronous Worker** architecture pattern.

## System Architecture

```mermaid
graph TB
    subgraph "Client Layer"
        Web[React Web App]
        API_Client[API Clients]
    end
    
    subgraph "Application Layer"
        Gateway[API Gateway / Load Balancer]
        
        subgraph "FastAPI Application"
            Auth[Auth Module]
            Docs[Document Module]
            Search[Search Module]
            RAG[RAG Module]
            Conv[Conversation Module]
        end
    end
    
    subgraph "Data Layer"
        PG[(PostgreSQL + pgvector)]
        Redis[(Redis)]
        Storage[File Storage]
    end
    
    subgraph "Worker Layer"
        Celery[Celery Workers]
        
        subgraph "Processing Tasks"
            Parse[Document Parsing]
            Chunk[Chunking]
            Embed[Embedding]
            Index[Indexing]
            Eval[Evaluation]
        end
    end
    
    subgraph "External Services"
        LLM[LLM Providers]
        Embed_Model[Embedding Models]
    end
    
    Web --> Gateway
    API_Client --> Gateway
    Gateway --> Auth
    Gateway --> Docs
    Gateway --> Search
    Gateway --> RAG
    Gateway --> Conv
    
    Auth --> PG
    Auth --> Redis
    Docs --> PG
    Docs --> Storage
    Search --> PG
    RAG --> PG
    RAG --> LLM
    Conv --> PG
    
    Celery --> PG
    Celery --> Redis
    Celery --> Storage
    Celery --> Embed_Model
    
    Parse --> Storage
    Chunk --> PG
    Embed --> PG
    Embed --> Embed_Model
    Index --> PG
    Eval --> PG
```

## Backend Architecture

### Layered Architecture

The backend follows a strict layered architecture:

```mermaid
graph TD
    A[API Routes Layer] --> B[Service Layer]
    B --> C[Repository Layer]
    C --> D[Database Layer]
    
    A2[API Routes] --> |Request/Response Schemas| B
    B --> |Domain Models| C
    C --> |SQLAlchemy Models| D
    
    style A fill:#e1f5ff
    style B fill:#fff4e1
    style C fill:#ffe1f5
    style D fill:#e1ffe1
```

### Module Structure

Implemented in Phase 1 (modules are added only when a phase needs them):

```
backend/
├── app/
│   ├── main.py             # Application factory (create_app / get_app)
│   ├── config.py           # Pydantic Settings, fail-fast validation
│   ├── dependencies.py     # FastAPI dependency providers
│   ├── exceptions.py       # AppError base class
│   ├── redis.py            # Redis client factory and probe
│   ├── api/
│   │   ├── router.py       # /api/v1 router (+ root health routes for orchestrators)
│   │   ├── routes/         # Route handlers (orchestration only)
│   │   ├── middleware/     # Request-context (request ID, access log) middleware
│   │   ├── error_handlers.py
│   │   └── responses.py    # Response/error envelope builders
│   ├── schemas/            # Pydantic API schemas
│   ├── services/           # Auth, Collection, Document, Processing and Health services
│   ├── models/             # SQLAlchemy models (User, Role, RefreshToken)
│   ├── db/                 # Base, async engine/session, DB probe, repositories/
│   ├── security/           # Passwords, JWT, refresh tokens, denylist, rate limit, RBAC, file validation
│   ├── core/documents/     # Types, lifecycle, processing errors, normalisation, script profile
│   ├── parsers/            # DocumentExtractor interface and PDF/DOCX/text/Markdown extractors
│   ├── observability/      # JSON logging, request-ID context
│   ├── workers/            # Celery app, queue dispatcher, document tasks and recovery sweep
│   └── storage/            # StorageProvider interface, local filesystem backend
├── alembic/                # Migrations (0001 pgvector, 0002 auth, 0003 documents/collections, 0004 processing)
└── tests/                  # unit, api, integration
```

Planned layers added in later phases: `repositories/` (data access), `models/`, `core/`
(domain logic), `parsers/`, `embeddings/`, `llm/`, `retrieval/`, `security/`.

Dependency direction: `api -> services -> (db | redis | storage)`. Infrastructure modules
(`db`, `redis`, `storage`, `observability`) never import from `api` or `services`.

### Dependency Injection

```mermaid
graph LR
    Route[Route Handler]
    Service[Service]
    Repo[Repository]
    DB[Database Session]
    
    Route --> |Depends| Service
    Service --> |Depends| Repo
    Repo --> |Depends| DB
    
    style Route fill:#e1f5ff
    style Service fill:#fff4e1
    style Repo fill:#ffe1f5
    style DB fill:#e1ffe1
```

**Example**:
```python
# routes/documents.py
@router.post("/documents")
async def upload_document(
    file: UploadFile,
    current_user: User = Depends(get_current_user),
    doc_service: DocumentService = Depends(get_document_service),
):
    return await doc_service.upload_document(file, current_user.id)

# services/document_service.py
class DocumentService:
    def __init__(self, doc_repo: DocumentRepository):
        self.doc_repo = doc_repo

# repositories/document_repository.py
class DocumentRepository:
    def __init__(self, db: AsyncSession):
        self.db = db
```

## Frontend Architecture

### Component Structure

```
frontend/
├── src/
│   ├── components/       # Reusable UI components
│   │   ├── common/       # Buttons, inputs, modals
│   │   ├── layout/       # Layout components
│   │   └── features/     # Feature-specific components
│   │
│   ├── pages/            # Page components
│   │   ├── Documents/
│   │   ├── Search/
│   │   ├── Conversations/
│   │   └── Settings/
│   │
│   ├── hooks/            # Custom React hooks
│   │   ├── useAuth.ts
│   │   ├── useDocuments.ts
│   │   └── useSearch.ts
│   │
│   ├── services/         # API client services
│   │   ├── api.ts
│   │   ├── authApi.ts
│   │   └── documentApi.ts
│   │
│   ├── store/            # State management
│   │   ├── authSlice.ts
│   │   └── documentSlice.ts
│   │
│   └── types/            # TypeScript types
│       ├── document.ts
│       ├── search.ts
│       └── conversation.ts
```

### State Management

```mermaid
graph TD
    Component[React Component]
    Hook[Custom Hook]
    API[API Service]
    Store[Redux Store / Context]
    
    Component --> Hook
    Hook --> API
    Hook --> Store
    API --> |Response| Hook
    Store --> |State| Hook
    Hook --> |Props| Component
    
    style Component fill:#e1f5ff
    style Hook fill:#fff4e1
    style API fill:#ffe1f5
    style Store fill:#e1ffe1
```

## Worker Architecture

### Celery Task Hierarchy

```mermaid
graph TD
    Upload[Document Upload]
    
    Upload --> |Trigger| Pipeline[Processing Pipeline]
    
    Pipeline --> Parse[Parse Document]
    Parse --> |Success| Chunk[Chunk Document]
    Parse --> |Failure| Error[Handle Error]
    
    Chunk --> |Success| Embed[Generate Embeddings]
    Chunk --> |Failure| Error
    
    Embed --> |Success| Index[Index in Vector DB]
    Embed --> |Failure| Error
    
    Index --> |Success| Ready[Document Ready]
    Index --> |Failure| Error
    
    Ready --> Eval[Run Evaluation - Optional]
    
    style Upload fill:#e1f5ff
    style Pipeline fill:#fff4e1
    style Parse fill:#ffe1f5
    style Chunk fill:#e1ffe1
    style Embed fill:#e1f5ff
    style Index fill:#fff4e1
    style Ready fill:#e1ffe1
```

### Task Queue Configuration

```mermaid
graph LR
    subgraph "Task Queues"
        Default[Default Queue]
        Processing[Processing Queue]
        Evaluation[Evaluation Queue]
    end
    
    subgraph "Workers"
        Worker1[Worker 1]
        Worker2[Worker 2]
        Worker3[Worker 3]
    end
    
    Default --> Worker1
    Processing --> Worker2
    Evaluation --> Worker3
    
    style Default fill:#e1f5ff
    style Processing fill:#fff4e1
    style Evaluation fill:#ffe1f5
```

## Data Flow

### Document Ingestion Flow

```mermaid
sequenceDiagram
    participant User
    participant API
    participant Service
    participant DB
    participant Storage
    participant Queue
    participant Worker
    
    User->>API: Upload document
    API->>Service: Validate & process
    Service->>Storage: Store file
    Service->>DB: Create document record
    Service->>Queue: Queue processing task
    API-->>User: Return document ID (pending)
    
    Queue->>Worker: Execute task
    Worker->>Storage: Retrieve file
    Worker->>Worker: Parse document
    Worker->>Worker: Chunk text
    Worker->>Worker: Generate embeddings
    Worker->>DB: Store chunks + vectors
    Worker->>DB: Update status to "ready"
    
    User->>API: Check document status
    API->>DB: Query document
    API-->>User: Return status "ready"
```

### RAG Query Flow

```mermaid
sequenceDiagram
    participant User
    participant API
    participant RAG_Service
    participant Search_Service
    participant DB
    participant LLM
    
    User->>API: Ask question
    API->>RAG_Service: Process query
    RAG_Service->>Search_Service: Retrieve context
    Search_Service->>DB: Hybrid search
    DB-->>Search_Service: Top-k chunks
    Search_Service-->>RAG_Service: Ranked chunks
    
    RAG_Service->>RAG_Service: Build prompt
    RAG_Service->>LLM: Generate answer
    LLM-->>RAG_Service: Answer text
    RAG_Service->>RAG_Service: Map citations
    RAG_Service-->>API: Answer + citations
    API-->>User: Response with citations
```

### Authentication Flow

```mermaid
sequenceDiagram
    participant C as Client
    participant API
    participant Auth as AuthService
    participant DB as PostgreSQL
    participant R as Redis

    C->>API: POST /auth/login (email, password)
    API->>R: Rate limit (client IP, account)
    API->>Auth: login()
    Auth->>DB: Find user by email
    Auth->>Auth: bcrypt verify (dummy hash if unknown)
    Auth->>DB: Store SHA-256 of new refresh token (new family)
    Auth-->>C: Access JWT (15 min) + opaque refresh token

    C->>API: Request with Bearer access token
    API->>Auth: authenticate()
    Auth->>Auth: Verify signature, exp, iss, aud, type
    Auth->>R: Denylist check (jti)
    Auth->>DB: Load active user and role
    Auth-->>API: Current user (role from DB)

    C->>API: POST /auth/refresh
    API->>Auth: refresh()
    Auth->>DB: Lock token row by hash
    alt token already used or revoked
        Auth->>DB: Revoke whole token family
        Auth-->>C: 401
    else valid
        Auth->>DB: Revoke old token, insert new token in same family
        Auth-->>C: New access + refresh tokens
    end

    C->>API: POST /auth/logout
    Auth->>DB: Revoke token family
    Auth->>R: Denylist access token jti until expiry
    Auth-->>C: 204
```

## Storage Architecture

### File Storage

```mermaid
graph TB
    subgraph "Storage Abstraction"
        Interface[Storage Interface]
        Local[Local Storage]
        S3[S3-compatible Storage]
    end
    
    subgraph "File Organization"
        Uploads[/uploads/]
        Documents[/documents/]
        Temp[/temp/]
    end
    
    Interface --> Local
    Interface --> S3
    
    Local --> Uploads
    Local --> Documents
    Local --> Temp
    
    S3 --> |Future| Bucket[S3 Bucket]
    
    style Interface fill:#e1f5ff
    style Local fill:#fff4e1
    style S3 fill:#ffe1f5
```

**Object Keys**: opaque and generated (`documents/{2 hex}/{32 hex}`). They contain no user ID, document
name, version or file name, so they can be neither guessed nor used to traverse a path. Ownership and
display names live only in PostgreSQL. (This replaces the earlier `/{user_id}/{document_id}/{version}/{filename}` sketch; see DEC-018.)

**Interface** (`app/storage/base.py`): `save(key, chunks)` streams bytes in and returns size and SHA-256;
`open(key)` streams bytes out; `size`, `exists` and `delete` complete it. The local filesystem backend is
implemented; an S3-compatible backend only needs to implement the same five methods.

### Document Lifecycle

```mermaid
stateDiagram-v2
    [*] --> pending: upload accepted
    pending --> parsing: worker claims it
    parsing --> ready: text extracted and stored
    parsing --> pending: transient failure, retry
    parsing --> failed: permanent failure or retries exhausted
    pending --> failed
    ready --> pending: re-process
    failed --> pending: manual retry
    pending --> [*]: delete
    parsing --> [*]: delete
    ready --> [*]: delete
    failed --> [*]: delete
```

In the current pipeline `pending` means *uploaded and queued* and `parsing` means *being processed*
(extract, normalise, persist). `ready` means the document's text has been extracted and stored; it does not yet mean
searchable. The `chunking`, `embedding` and `indexing` states remain defined for later phases, and the
temporary `parsing -> ready` shortcut will be replaced by `parsing -> chunking` when they arrive.
Transitions are enforced by `ensure_transition` (`app/core/documents/lifecycle.py`) and, for the worker,
by guarded `UPDATE ... WHERE status = ...` statements. `error_message` and `failure_reason` are only allowed
while `failed` (database CHECK). Deletion is a hard delete, so there is no `deleted` status.

### Upload Flow

```mermaid
sequenceDiagram
    participant C as Client
    participant API
    participant Svc as DocumentService
    participant S as StorageProvider
    participant DB as PostgreSQL

    C->>API: POST /documents (multipart)
    API->>API: Body size cap, authenticate, rate limit
    API->>Svc: upload(user, file)
    Svc->>Svc: Validate type, size, structure; SHA-256
    Svc->>DB: Duplicate check (user, checksum)
    Svc->>S: save(generated key, stream)
    S-->>Svc: size + SHA-256 (verified)
    Svc->>DB: INSERT document + collection links, COMMIT
    alt insert fails
        Svc->>S: delete(key)
    end
    Svc-->>C: 201 Document (status pending)
```

### Processing Pipeline

```mermaid
sequenceDiagram
    participant API
    participant Q as Redis (Celery queue)
    participant W as Celery worker
    participant DB as PostgreSQL
    participant S as StorageProvider

    API->>DB: INSERT document (pending), COMMIT
    API->>Q: enqueue process_document(id)
    Note over API,Q: if the broker is down the upload still succeeds; the sweep re-queues
    Q->>W: deliver task
    W->>DB: claim: UPDATE ... SET status=parsing WHERE status=pending
    alt not claimable (done, deleted or owned by another worker)
        W-->>Q: acknowledge and skip
    else claimed
        W->>S: read stored file (size verified)
        W->>W: extract (PyMuPDF / python-docx / text), enforcing limits
        W->>W: normalise Unicode and whitespace per section
        W->>DB: replace sections, set counts and metadata, status=ready
        alt transient failure and attempts remain
            W->>DB: status=pending, retry after backoff
        else permanent failure
            W->>DB: status=failed, failure_reason, safe message
        end
    end
    loop every 60 s (Celery beat)
        W->>DB: release stalled parsing, re-queue lost pending
    end
```

The API never parses a document. A worker process handles one document at a time per task slot, with a
cooperative time budget (checked between pages/sections), Celery soft and hard time limits as a backstop,
and recycling after a number of tasks or a memory threshold.

### Extraction and Normalization

Extractors share one interface (`DocumentExtractor`, `app/parsers/base.py`) and return ordered sections
of raw text plus untrusted file properties. The pipeline then normalises each section independently, so page and
heading boundaries are never merged away.

| Format | Library | Sections produced | Notes |
|--------|---------|-------------------|-------|
| PDF | PyMuPDF | one `page` section per page (1-based `page_number`), blank pages kept | encrypted PDFs rejected; text layer only (no OCR) |
| DOCX | python-docx | one `section` per heading (levels 1-9, Title = 1) plus an optional leading `body`; table rows are kept as one line per row; headers, footers, comments and macros are ignored |
| Markdown | native | one `section` per ATX heading (`#`..`######`), headings inside code fences ignored | source is kept verbatim; nothing is rendered |
| TXT | native | one `body` section | UTF-8 (BOM tolerated) |

Normalisation (`app/core/documents/normalization.py`) is deliberately conservative: Unicode NFC (not NFKC),
`\r\n`/`\r`/U+2028/U+2029 to `\n`, Unicode spaces to a plain space, runs of blank lines collapsed to one, and invisible or
control artefacts removed (NUL, controls, soft hyphen, zero-width space, BOM, bidi overrides). It preserves case,
punctuation, combining marks and, importantly, ZWJ/ZWNJ (U+200D/U+200C), which Bengali conjuncts depend on. PDF and DOCX also
collapse repeated spaces inside lines; plain text and Markdown keep interior spacing and indentation. There is no lowercasing,
stemming, stop-word removal or translation. Bengali "nukta" letters that Unicode defines as composition exclusions (for example U+09DF) are stored in
their canonical decomposed form; the same normalisation must be applied to queries in later phases.

A dependency-free **script profile** (Bengali / Latin / other shares and a primary script of `bengali`, `latin`, `mixed`, `other` or `unknown`) is
stored with each document. It is a heuristic about writing systems, not language identification.

### Content Model

Processed text lives in `document_sections` (one row per page or section: `ordinal`, `kind`, `page_number`,
`heading`, `heading_level`, `text`, `char_count`), referenced by `document_id` with cascade delete. A later chunker
reads sections in order and can attach each chunk to a page or heading, so citations keep their source location.
Per-document counts, timestamps and metadata sit on `documents` (`page_count`, `character_count`,
`processing_started_at`, `processing_completed_at`, `processing_metadata` with extractor, processing version, duration, script profile and sanitised file properties).
Re-processing replaces a document's sections inside the same transaction that marks it `ready`, so retries never duplicate content.

### Failure Handling and Recovery

- Every failure is classified (`FailureReason`): `storage_missing`, `storage_unavailable`, `corrupt_document`, `encrypted_document`, `unsupported_format`, `empty_document`, `too_many_pages`, `content_too_large`, `timeout`, `extraction_failed`, `database_error`, `retries_exhausted`. Users see the code and a fixed, safe message, never exception text.
- Only transient classes (`storage_unavailable`, `database_error`) are retried automatically, with exponential backoff, up to `PROCESSING_MAX_ATTEMPTS`. Everything else fails once, so a poisonous document cannot loop.
- A worker claims a document with a single guarded `UPDATE`, so duplicate or redelivered tasks cannot process it twice. A worker that dies mid-task leaves the document in `parsing`; once it is older than `PROCESSING_STALE_AFTER_SECONDS` it can be re-claimed, and the beat sweep releases it (or fails it after the attempt budget). Pending documents whose queue message was lost are re-queued by the same sweep.
- `POST /documents/{id}/retry` lets the owner re-queue a failed document with a fresh attempt budget.

### Database Storage

PostgreSQL with pgvector extension:

**Relational Data**:
- Users, roles, permissions
- Documents, collections, versions
- Conversations, messages
- Evaluation runs and results

**Vector Data**:
- Document chunk embeddings
- Vector index for similarity search

### Cache Storage

Redis used for:
- Session storage
- API response caching
- Celery task broker
- Rate limiting counters

## AI Architecture

### Embedding Architecture

```mermaid
graph TB
    subgraph "Embedding Provider Interface"
        Interface[EmbeddingProvider]
        Local[Local Model]
        OpenAI[OpenAI Embeddings]
        Cohere[Cohere Embeddings]
    end
    
    subgraph "Local Model Stack"
        ST[sentence-transformers]
        Model[Embedding Model]
        GPU[GPU/CPU Inference]
    end
    
    Interface --> Local
    Interface --> OpenAI
    Interface --> Cohere
    
    Local --> ST
    ST --> Model
    Model --> GPU
    
    style Interface fill:#e1f5ff
    style Local fill:#fff4e1
    style ST fill:#ffe1f5
```

### LLM Architecture

```mermaid
graph TB
    subgraph "LLM Provider Interface"
        Interface[LLMProvider]
        OpenAI[OpenAI GPT]
        Anthropic[Claude]
        Ollama[Local Ollama]
    end
    
    subgraph "Prompt Management"
        Templates[Prompt Templates]
        Context[Context Builder]
        TokenBudget[Token Budgeting]
    end
    
    Interface --> OpenAI
    Interface --> Anthropic
    Interface --> Ollama
    
    Context --> Templates
    TokenBudget --> Context
    Context --> Interface
    
    style Interface fill:#e1f5ff
    style Templates fill:#fff4e1
    style Context fill:#ffe1f5
```

### Retrieval Architecture

```mermaid
graph TB
    Query[User Query]
    
    subgraph "Retrieval Pipeline"
        Semantic[Semantic Search]
        Keyword[Keyword Search]
        Hybrid[Hybrid Scoring]
        Rerank[Reranker]
    end
    
    Results[Ranked Results]
    
    Query --> Semantic
    Query --> Keyword
    Semantic --> |Vectors| Hybrid
    Keyword --> |Full-text| Hybrid
    Hybrid --> Rerank
    Rerank --> Results
    
    style Query fill:#e1f5ff
    style Semantic fill:#fff4e1
    style Keyword fill:#ffe1f5
    style Hybrid fill:#e1ffe1
    style Rerank fill:#e1f5ff
```

## Failure Boundaries

### Failure Isolation

```mermaid
graph TB
    subgraph "Failure Domains"
        API_Layer[API Layer]
        Worker_Layer[Worker Layer]
        Data_Layer[Data Layer]
        External_Layer[External Services]
    end
    
    API_Layer --> |Handles| API_Errors[Request Errors]
    Worker_Layer --> |Handles| Worker_Errors[Processing Errors]
    Data_Layer --> |Handles| Data_Errors[Data Errors]
    External_Layer --> |Handles| External_Errors[Provider Errors]
    
    API_Errors --> |Graceful Degradation| User[User Notification]
    Worker_Errors --> |Retry + Alert| Queue[Task Queue]
    Data_Errors --> |Transaction Rollback| DB[Database]
    External_Errors --> |Fallback + Cache| Cache[Cache Layer]
    
    style API_Layer fill:#ffe1e1
    style Worker_Layer fill:#ffe1e1
    style Data_Layer fill:#ffe1e1
    style External_Layer fill:#ffe1e1
```

### Error Handling Strategy

| Failure Type | Handling | User Impact |
|--------------|----------|-------------|
| API Request Error | Return error response | User sees error message |
| Database Error | Rollback transaction | Operation fails gracefully |
| Worker Error | Retry with exponential backoff | Processing delayed |
| LLM Provider Error | Try alternate provider or return cached | Answer may be unavailable |
| Embedding Error | Queue for retry | Processing delayed |
| File Storage Error | Mark document as failed | Upload fails, user notified |

## Scalability Considerations

### Horizontal Scaling Points

```mermaid
graph LR
    subgraph "Scalable Components"
        API_Instances[API Instances]
        Worker_Instances[Worker Instances]
    end
    
    subgraph "Stateless Design"
        NoSession[No Local Sessions]
        NoLocalState[No Local State]
    end
    
    subgraph "Shared Resources"
        LoadBalancer[Load Balancer]
        DB_Cluster[(DB Cluster)]
        Redis_Cluster[(Redis Cluster)]
    end
    
    LoadBalancer --> API_Instances
    API_Instances --> NoSession
    API_Instances --> NoLocalState
    API_Instances --> DB_Cluster
    API_Instances --> Redis_Cluster
    
    Worker_Instances --> DB_Cluster
    Worker_Instances --> Redis_Cluster
    
    style API_Instances fill:#e1f5ff
    style Worker_Instances fill:#fff4e1
    style DB_Cluster fill:#ffe1f5
    style Redis_Cluster fill:#e1ffe1
```

### Scalability Path

**Current (Phase 0-16)**:
- Single API instance
- Single worker instance
- Single database instance
- Single Redis instance

**Future Scaling**:
1. Multiple API instances behind load balancer
2. Multiple workers for processing throughput
3. Database read replicas
4. Redis cluster for high availability
5. CDN for static assets

## Security Architecture

### Security Layers

```mermaid
graph TB
    subgraph "Security Defense in Depth"
        Network[Network Security]
        App[Application Security]
        Data[Data Security]
        Auth[Authentication/Authorization]
    end
    
    Network --> HTTPS[HTTPS/TLS]
    Network --> CORS[CORS Policy]
    Network --> RateLimit[Rate Limiting]
    
    App --> InputValidation[Input Validation]
    App --> Sanitization[Output Sanitization]
    App --> FileValidation[File Validation]
    
    Data --> Encryption[Encryption at Rest - Future]
    Data --> AccessControl[Access Control]
    
    Auth --> JWT[JWT Authentication]
    Auth --> RBAC[Role-Based Access Control]
    Auth --> TokenRefresh[Token Refresh]
    
    style Network fill:#ffe1e1
    style App fill:#fff4e1
    style Data fill:#e1ffe1
    style Auth fill:#e1f5ff
```

### Authorization Model

```mermaid
graph LR
    User[User]
    Role[Role]
    Permission[Permission]
    Resource[Resource]
    
    User --> |Has| Role
    Role --> |Has| Permission
    Permission --> |Grants Access| Resource
    
    style User fill:#e1f5ff
    style Role fill:#fff4e1
    style Permission fill:#ffe1f5
    style Resource fill:#e1ffe1
```

## Observability Architecture

### Logging Pipeline

```mermaid
graph LR
    App[Application]
    StructuredLog[Structured Logging]
    RequestID[Request ID]
    
    App --> StructuredLog
    StructuredLog --> RequestID
    RequestID --> |JSON Format| Logs[Log Aggregation]
    
    style App fill:#e1f5ff
    style StructuredLog fill:#fff4e1
    style RequestID fill:#ffe1f5
    style Logs fill:#e1ffe1
```

### Monitoring Points

- **Application Metrics**: Request count, latency, error rate
- **Database Metrics**: Query time, connection pool, slow queries
- **Worker Metrics**: Task throughput, failure rate, queue depth
- **Search Metrics**: Query latency, result count, cache hit rate
- **LLM Metrics**: Token usage, latency, cost

## Deployment Architecture

### Development Environment

```mermaid
graph TB
    Dev[Developer Machine]
    
    subgraph "Docker Compose"
        API[FastAPI Container]
        Worker[Celery Container]
        DB[PostgreSQL Container]
        Redis[Redis Container]
        Frontend[React Container]
    end
    
    Dev --> API
    Dev --> Frontend
    API --> DB
    API --> Redis
    Worker --> DB
    Worker --> Redis
    
    style Dev fill:#e1f5ff
    style API fill:#fff4e1
    style Worker fill:#ffe1f5
    style DB fill:#e1ffe1
```

The development stack is defined in `docker-compose.yml`: `postgres` (pgvector image),
`redis` (password-protected), a one-shot `migrate` job, `backend`, `celery_worker` and
`frontend`. The API starts only after migrations complete and its dependencies are healthy.
Database and Redis ports are published on `127.0.0.1` only.

### Production Environment (Future)

```mermaid
graph TB
    Users[Users]
    
    subgraph "Cloud Infrastructure"
        CDN[CDN]
        LB[Load Balancer]
        
        subgraph "API Tier"
            API1[API Instance 1]
            API2[API Instance 2]
        end
        
        subgraph "Worker Tier"
            Worker1[Worker 1]
            Worker2[Worker 2]
        end
        
        subgraph "Data Tier"
            DB_Primary[(DB Primary)]
            DB_Replica[(DB Replica)]
            Redis_Cluster[(Redis)]
        end
        
        subgraph "Storage"
            S3[S3-compatible Storage]
        end
    end
    
    Users --> CDN
    CDN --> LB
    LB --> API1
    LB --> API2
    API1 --> DB_Primary
    API2 --> DB_Primary
    API1 --> DB_Replica
    API2 --> DB_Replica
    API1 --> Redis_Cluster
    API2 --> Redis_Cluster
    API1 --> S3
    
    Worker1 --> DB_Primary
    Worker2 --> DB_Primary
    Worker1 --> Redis_Cluster
    Worker2 --> Redis_Cluster
    Worker1 --> S3
    
    style Users fill:#e1f5ff
    style CDN fill:#fff4e1
    style LB fill:#ffe1f5
    style API1 fill:#e1ffe1
```

---

*This architecture is designed for clarity, maintainability, and scalability. It follows the principle of starting simple and extracting services only when genuinely necessary.*
