# Database Schema

## Overview

This document defines the complete database schema for the Intelligent Document Processing & RAG Platform using PostgreSQL with pgvector extension.

## Entity-Relationship Diagram

```mermaid
erDiagram
    User ||--o{ Document : owns
    User ||--o{ Collection : creates
    User ||--o{ Conversation : has
    User ||--o{ RefreshToken : has
    User }o--|| Role : has
    
    Role ||--o{ User : contains
    
    Document }o--o{ Collection : belongs_to
    Document ||--o{ DocumentVersion : has
    Document ||--o{ DocumentChunk : contains
    
    DocumentChunk ||--o{ Citation : referenced_in
    
    Conversation ||--o{ Message : contains
    Message ||--o{ Citation : includes
    
    EvaluationRun ||--o{ EvaluationResult : produces
    
    User {
        uuid id PK
        string email UK
        string username UK
        string hashed_password
        boolean is_active
        boolean is_superuser
        datetime created_at
        datetime updated_at
        datetime deleted_at
    }
    
    Role {
        int id PK
        string name UK
        string description
        datetime created_at
    }
    
    RefreshToken {
        uuid id PK
        uuid user_id FK
        string token_hash UK
        datetime expires_at
        datetime revoked_at
        datetime created_at
    }
    
    Collection {
        uuid id PK
        uuid user_id FK
        string name
        text description
        datetime created_at
        datetime updated_at
    }
    
    Document {
        uuid id PK
        uuid user_id FK
        string filename
        string storage_path
        string file_type
        bigint file_size
        string status
        text error_message
        int page_count
        int word_count
        jsonb metadata
        datetime created_at
        datetime updated_at
    }
    
    DocumentVersion {
        uuid id PK
        uuid document_id FK
        int version_number
        string storage_path
        bigint file_size
        string checksum
        datetime created_at
    }
    
    DocumentChunk {
        uuid id PK
        uuid document_id FK
        int chunk_index
        text content
        int page_number
        jsonb metadata
        vector embedding
        datetime created_at
    }
    
    Conversation {
        uuid id PK
        uuid user_id FK
        string title
        datetime created_at
        datetime updated_at
    }
    
    Message {
        uuid id PK
        uuid conversation_id FK
        string role
        text content
        jsonb metadata
        datetime created_at
    }
    
    Citation {
        uuid id PK
        uuid message_id FK
        uuid chunk_id FK
        int start_offset
        int end_offset
        string citation_text
        datetime created_at
    }
    
    EvaluationRun {
        uuid id PK
        uuid user_id FK
        string name
        string status
        jsonb config
        jsonb results_summary
        datetime started_at
        datetime completed_at
        datetime created_at
    }
    
    EvaluationResult {
        uuid id PK
        uuid evaluation_run_id FK
        string metric_name
        float metric_value
        jsonb details
        datetime created_at
    }
```

## Table Definitions

### Users & Authentication

#### users

Primary user table with soft delete support.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | UUID | PRIMARY KEY, DEFAULT gen_random_uuid() | Unique identifier |
| email | VARCHAR(255) | UNIQUE, NOT NULL | User email address |
| username | VARCHAR(50) | UNIQUE, NOT NULL | Display username |
| hashed_password | VARCHAR(255) | NOT NULL | Bcrypt hashed password |
| is_active | BOOLEAN | NOT NULL, DEFAULT true | Account active status |
| is_superuser | BOOLEAN | NOT NULL, DEFAULT false | Admin flag |
| role_id | INTEGER | FOREIGN KEY, NOT NULL | Reference to role |
| created_at | TIMESTAMP WITH TIME ZONE | NOT NULL, DEFAULT NOW() | Creation timestamp |
| updated_at | TIMESTAMP WITH TIME ZONE | NOT NULL, DEFAULT NOW() | Last update timestamp |
| deleted_at | TIMESTAMP WITH TIME ZONE | NULLABLE | Soft delete timestamp |

**Indexes**:
- `idx_users_email` on `email` (for login lookups)
- `idx_users_username` on `username` (for profile lookups)
- `idx_users_role_id` on `role_id` (foreign key)

**Constraints**:
- `chk_users_email_format` - Valid email format
- `chk_users_username_length` - Username 3-50 characters

#### roles

Role definitions for RBAC.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | SERIAL | PRIMARY KEY | Auto-increment ID |
| name | VARCHAR(50) | UNIQUE, NOT NULL | Role name (user, admin) |
| description | TEXT | NULLABLE | Role description |
| created_at | TIMESTAMP WITH TIME ZONE | NOT NULL, DEFAULT NOW() | Creation timestamp |

**Default Data**:
```sql
INSERT INTO roles (name, description) VALUES 
    ('user', 'Standard user with basic permissions'),
    ('admin', 'Administrator with full permissions');
```

#### refresh_tokens

Refresh tokens for JWT authentication.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | UUID | PRIMARY KEY, DEFAULT gen_random_uuid() | Unique identifier |
| user_id | UUID | FOREIGN KEY, NOT NULL | Reference to user |
| token_hash | VARCHAR(255) | UNIQUE, NOT NULL | SHA-256 hash of token |
| expires_at | TIMESTAMP WITH TIME ZONE | NOT NULL | Token expiration |
| revoked_at | TIMESTAMP WITH TIME ZONE | NULLABLE | Revocation timestamp |
| created_at | TIMESTAMP WITH TIME ZONE | NOT NULL, DEFAULT NOW() | Creation timestamp |

**Indexes**:
- `idx_refresh_tokens_user_id` on `user_id` (for user's tokens)
- `idx_refresh_tokens_token_hash` on `token_hash` (unique lookup)
- `idx_refresh_tokens_expires_at` on `expires_at` (for cleanup)

**Constraints**:
- `fk_refresh_tokens_user` FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE

### Documents & Collections

#### collections

Document collections for organization.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | UUID | PRIMARY KEY, DEFAULT gen_random_uuid() | Unique identifier |
| user_id | UUID | FOREIGN KEY, NOT NULL | Owner user |
| name | VARCHAR(255) | NOT NULL | Collection name |
| description | TEXT | NULLABLE | Collection description |
| created_at | TIMESTAMP WITH TIME ZONE | NOT NULL, DEFAULT NOW() | Creation timestamp |
| updated_at | TIMESTAMP WITH TIME ZONE | NOT NULL, DEFAULT NOW() | Last update timestamp |

**Indexes**:
- `idx_collections_user_id` on `user_id`
- `idx_collections_name` on `name` (for search)

**Constraints**:
- `fk_collections_user` FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE

#### documents

Main document metadata table.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | UUID | PRIMARY KEY, DEFAULT gen_random_uuid() | Unique identifier |
| user_id | UUID | FOREIGN KEY, NOT NULL | Owner user |
| filename | VARCHAR(255) | NOT NULL | Original filename |
| storage_path | VARCHAR(500) | NOT NULL | Server storage path |
| file_type | VARCHAR(10) | NOT NULL | pdf, docx, txt, md |
| file_size | BIGINT | NOT NULL | File size in bytes |
| status | VARCHAR(20) | NOT NULL, DEFAULT 'pending' | Processing status |
| error_message | TEXT | NULLABLE | Error if processing failed |
| page_count | INTEGER | NULLABLE | Number of pages |
| word_count | INTEGER | NULLABLE | Total word count |
| metadata | JSONB | DEFAULT '{}' | Additional metadata |
| created_at | TIMESTAMP WITH TIME ZONE | NOT NULL, DEFAULT NOW() | Upload timestamp |
| updated_at | TIMESTAMP WITH TIME ZONE | NOT NULL, DEFAULT NOW() | Last update timestamp |

**Status Values**:
- `pending` - Uploaded, not yet processed
- `parsing` - Being parsed
- `chunking` - Being chunked
- `embedding` - Generating embeddings
- `indexing` - Indexing vectors
- `ready` - Ready for search and RAG
- `failed` - Processing failed

**Indexes**:
- `idx_documents_user_id` on `user_id`
- `idx_documents_status` on `status`
- `idx_documents_file_type` on `file_type`
- `idx_documents_created_at` on `created_at` DESC
- `idx_documents_metadata` on `metadata` USING GIN (for JSON queries)

**Constraints**:
- `fk_documents_user` FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
- `chk_documents_status` CHECK (status IN ('pending', 'parsing', 'chunking', 'embedding', 'indexing', 'ready', 'failed'))
- `chk_documents_file_type` CHECK (file_type IN ('pdf', 'docx', 'txt', 'md'))
- `chk_documents_file_size` CHECK (file_size > 0 AND file_size <= 52428800) -- 50MB max

#### document_collections

Many-to-many relationship between documents and collections.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| document_id | UUID | FOREIGN KEY, NOT NULL | Reference to document |
| collection_id | UUID | FOREIGN KEY, NOT NULL | Reference to collection |
| created_at | TIMESTAMP WITH TIME ZONE | NOT NULL, DEFAULT NOW() | Addition timestamp |

**Primary Key**: (document_id, collection_id)

**Indexes**:
- `idx_document_collections_collection_id` on `collection_id`

**Constraints**:
- `fk_document_collections_document` FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
- `fk_document_collections_collection` FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE

#### document_versions

Document version history.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | UUID | PRIMARY KEY, DEFAULT gen_random_uuid() | Unique identifier |
| document_id | UUID | FOREIGN KEY, NOT NULL | Reference to document |
| version_number | INTEGER | NOT NULL | Version sequence number |
| storage_path | VARCHAR(500) | NOT NULL | Storage path for this version |
| file_size | BIGINT | NOT NULL | File size in bytes |
| checksum | VARCHAR(64) | NOT NULL | SHA-256 checksum |
| created_at | TIMESTAMP WITH TIME ZONE | NOT NULL, DEFAULT NOW() | Upload timestamp |

**Indexes**:
- `idx_document_versions_document_id` on `document_id`
- `uq_document_versions_number` UNIQUE (document_id, version_number)

**Constraints**:
- `fk_document_versions_document` FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE

#### document_chunks

Text chunks with embeddings.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | UUID | PRIMARY KEY, DEFAULT gen_random_uuid() | Unique identifier |
| document_id | UUID | FOREIGN KEY, NOT NULL | Reference to document |
| chunk_index | INTEGER | NOT NULL | Position in document |
| content | TEXT | NOT NULL | Chunk text content |
| page_number | INTEGER | NULLABLE | Source page number |
| metadata | JSONB | DEFAULT '{}' | Chunk metadata |
| embedding | vector(384) | NULLABLE | Embedding vector |
| created_at | TIMESTAMP WITH TIME ZONE | NOT NULL, DEFAULT NOW() | Creation timestamp |

**Note**: Vector dimension (384) matches default embedding model (all-MiniLM-L6-v2). Configurable based on model choice.

**Indexes**:
- `idx_document_chunks_document_id` on `document_id`
- `idx_document_chunks_embedding` on `embedding` USING ivfflat (vector_cosine_ops) WITH (lists = 100)

**Constraints**:
- `fk_document_chunks_document` FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
- `uq_document_chunks_index` UNIQUE (document_id, chunk_index)

### Conversations & Messages

#### conversations

Conversation threads.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | UUID | PRIMARY KEY, DEFAULT gen_random_uuid() | Unique identifier |
| user_id | UUID | FOREIGN KEY, NOT NULL | Owner user |
| title | VARCHAR(255) | NULLABLE | Conversation title |
| created_at | TIMESTAMP WITH TIME ZONE | NOT NULL, DEFAULT NOW() | Creation timestamp |
| updated_at | TIMESTAMP WITH TIME ZONE | NOT NULL, DEFAULT NOW() | Last message timestamp |

**Indexes**:
- `idx_conversations_user_id` on `user_id`
- `idx_conversations_updated_at` on `updated_at` DESC

**Constraints**:
- `fk_conversations_user` FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE

#### messages

Individual messages in conversations.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | UUID | PRIMARY KEY, DEFAULT gen_random_uuid() | Unique identifier |
| conversation_id | UUID | FOREIGN KEY, NOT NULL | Reference to conversation |
| role | VARCHAR(20) | NOT NULL | user, assistant, system |
| content | TEXT | NOT NULL | Message content |
| metadata | JSONB | DEFAULT '{}' | Additional metadata (tokens, model, etc.) |
| created_at | TIMESTAMP WITH TIME ZONE | NOT NULL, DEFAULT NOW() | Message timestamp |

**Indexes**:
- `idx_messages_conversation_id` on `conversation_id`
- `idx_messages_created_at` on `created_at`

**Constraints**:
- `fk_messages_conversation` FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
- `chk_messages_role` CHECK (role IN ('user', 'assistant', 'system'))

#### citations

Citations linking messages to chunks.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | UUID | PRIMARY KEY, DEFAULT gen_random_uuid() | Unique identifier |
| message_id | UUID | FOREIGN KEY, NOT NULL | Reference to message |
| chunk_id | UUID | FOREIGN KEY, NOT NULL | Reference to chunk |
| start_offset | INTEGER | NULLABLE | Start position in message |
| end_offset | INTEGER | NULLABLE | End position in message |
| citation_text | TEXT | NULLABLE | Cited text snippet |
| created_at | TIMESTAMP WITH TIME ZONE | NOT NULL, DEFAULT NOW() | Creation timestamp |

**Indexes**:
- `idx_citations_message_id` on `message_id`
- `idx_citations_chunk_id` on `chunk_id`

**Constraints**:
- `fk_citations_message` FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
- `fk_citations_chunk` FOREIGN KEY (chunk_id) REFERENCES document_chunks(id) ON DELETE CASCADE

### Evaluation

#### evaluation_runs

Evaluation execution records.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | UUID | PRIMARY KEY, DEFAULT gen_random_uuid() | Unique identifier |
| user_id | UUID | FOREIGN KEY, NOT NULL | User who ran evaluation |
| name | VARCHAR(255) | NOT NULL | Evaluation run name |
| status | VARCHAR(20) | NOT NULL, DEFAULT 'pending' | Run status |
| config | JSONB | NOT NULL | Evaluation configuration |
| results_summary | JSONB | NULLABLE | Aggregated results |
| started_at | TIMESTAMP WITH TIME ZONE | NULLABLE | Start timestamp |
| completed_at | TIMESTAMP WITH TIME ZONE | NULLABLE | Completion timestamp |
| created_at | TIMESTAMP WITH TIME ZONE | NOT NULL, DEFAULT NOW() | Creation timestamp |

**Status Values**:
- `pending` - Queued
- `running` - In progress
- `completed` - Successfully completed
- `failed` - Failed with error

**Indexes**:
- `idx_evaluation_runs_user_id` on `user_id`
- `idx_evaluation_runs_status` on `status`
- `idx_evaluation_runs_created_at` on `created_at` DESC

**Constraints**:
- `fk_evaluation_runs_user` FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
- `chk_evaluation_runs_status` CHECK (status IN ('pending', 'running', 'completed', 'failed'))

#### evaluation_results

Individual metric results.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | UUID | PRIMARY KEY, DEFAULT gen_random_uuid() | Unique identifier |
| evaluation_run_id | UUID | FOREIGN KEY, NOT NULL | Reference to run |
| metric_name | VARCHAR(100) | NOT NULL | Metric identifier |
| metric_value | FLOAT | NOT NULL | Metric value |
| details | JSONB | DEFAULT '{}' | Additional details |
| created_at | TIMESTAMP WITH TIME ZONE | NOT NULL, DEFAULT NOW() | Creation timestamp |

**Indexes**:
- `idx_evaluation_results_run_id` on `evaluation_run_id`
- `idx_evaluation_results_metric_name` on `metric_name`

**Constraints**:
- `fk_evaluation_results_run` FOREIGN KEY (evaluation_run_id) REFERENCES evaluation_runs(id) ON DELETE CASCADE

## Database Functions & Triggers

### Automatic Timestamp Updates

```sql
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Apply to tables with updated_at
CREATE TRIGGER update_users_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_documents_updated_at BEFORE UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_collections_updated_at BEFORE UPDATE ON collections
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_conversations_updated_at BEFORE UPDATE ON conversations
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
```

### Automatic Conversation Update on Message

```sql
CREATE OR REPLACE FUNCTION update_conversation_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE conversations 
    SET updated_at = NOW() 
    WHERE id = NEW.conversation_id;
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_conversation_on_message AFTER INSERT ON messages
    FOR EACH ROW EXECUTE FUNCTION update_conversation_timestamp();
```

## Vector Index Configuration

### IVFFlat Index for Vector Search

```sql
-- Create IVFFlat index for cosine similarity
CREATE INDEX idx_document_chunks_embedding ON document_chunks 
USING ivfflat (embedding vector_cosine_ops) 
WITH (lists = 100);

-- Note: lists parameter should be sqrt(rows) for optimal performance
-- Adjust as dataset grows:
-- lists = 100 for up to 10,000 rows
-- lists = 1000 for up to 1,000,000 rows
```

### Alternative: HNSW Index

```sql
-- HNSW provides better recall but slower index build and more memory
CREATE INDEX idx_document_chunks_embedding_hnsw ON document_chunks 
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
```

## Data Retention & Cleanup

### Refresh Token Cleanup

```sql
-- Function to clean up expired/revoked tokens
CREATE OR REPLACE FUNCTION cleanup_refresh_tokens()
RETURNS void AS $$
BEGIN
    DELETE FROM refresh_tokens 
    WHERE expires_at < NOW() 
       OR revoked_at IS NOT NULL;
END;
$$ LANGUAGE plpgsql;

-- Run periodically via pg_cron or external scheduler
```

### Soft Delete Handling

```sql
-- Function to handle user soft delete
CREATE OR REPLACE FUNCTION soft_delete_user(user_uuid UUID)
RETURNS void AS $$
BEGIN
    UPDATE users 
    SET deleted_at = NOW(), 
        is_active = false,
        email = email || '_deleted_' || extract(epoch from NOW())
    WHERE id = user_uuid;
    
    -- Revoke all refresh tokens
    UPDATE refresh_tokens 
    SET revoked_at = NOW() 
    WHERE user_id = user_uuid AND revoked_at IS NULL;
END;
$$ LANGUAGE plpgsql;
```

## Connection Pooling Configuration

Recommended connection pool settings (in application):

```python
# SQLAlchemy async engine configuration
DATABASE_URL = "postgresql+asyncpg://user:pass@host/db"

engine = create_async_engine(
    DATABASE_URL,
    pool_size=10,           # Number of permanent connections
    max_overflow=20,        # Additional connections when pool exhausted
    pool_pre_ping=True,     # Verify connections before use
    pool_recycle=3600,      # Recycle connections after 1 hour
)
```

## Migration Strategy

All schema changes through Alembic migrations:

1. **Create migration**: `alembic revision --autogenerate -m "description"`
2. **Review generated migration**
3. **Test on development database**
4. **Apply to staging**
5. **Apply to production** (with backup)

**Migration Naming Convention**:
- `001_initial_schema.py`
- `002_add_document_versions.py`
- `003_add_evaluation_tables.py`
- etc.

## Backup Strategy

**Development**: Manual pg_dump

**Production** (future):
- Daily automated backups
- Point-in-time recovery enabled
- Backup retention: 30 days
- Backup testing: Monthly restore verification

---

*This schema is designed for the Phase 0-16 scope. Future enhancements may require additional tables or modifications.*
