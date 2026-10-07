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
│   ├── services/           # Service layer (HealthService so far)
│   ├── db/                 # Declarative base, async engine/session, DB probe
│   ├── observability/      # JSON logging, request-ID context
│   ├── workers/            # Celery app and tasks
│   └── storage/            # StorageProvider interface (no implementation yet)
├── alembic/                # Migrations (0001 enables pgvector)
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
    participant User
    participant API
    participant Auth_Service
    participant DB
    participant Redis
    
    User->>API: Login (email, password)
    API->>Auth_Service: Authenticate
    Auth_Service->>DB: Find user
    DB-->>Auth_Service: User data
    Auth_Service->>Auth_Service: Verify password
    Auth_Service->>Auth_Service: Generate tokens
    Auth_Service->>DB: Store refresh token (hashed)
    Auth_Service-->>API: Access + Refresh tokens
    API-->>User: Tokens in response
    
    User->>API: Request with access token
    API->>API: Validate token
    API->>Auth_Service: Get user from token
    Auth_Service-->>API: User data
    API->>API: Process request
    API-->>User: Response
    
    User->>API: Refresh access token
    API->>Auth_Service: Validate refresh token
    Auth_Service->>DB: Check refresh token
    DB-->>Auth_Service: Token valid
    Auth_Service->>Auth_Service: Generate new access token
    Auth_Service-->>API: New access token
    API-->>User: New token
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

**File Path Structure**:
```
/uploads/
  └── {user_id}/
      └── {document_id}/
          └── {version}/
              └── {filename}
```

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
