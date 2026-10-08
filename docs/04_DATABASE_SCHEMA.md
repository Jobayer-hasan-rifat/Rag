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
    Document ||--o{ DocumentSection : has
    Document ||--o{ DocumentVersion : has
    Document ||--o{ DocumentChunk : contains
    
    DocumentChunk ||--o{ Citation : referenced_in
    
    Conversation ||--o{ Message : contains
    Message ||--o{ Citation : includes
    
    EvaluationRun ||--o{ EvaluationResult : produces
    
    User {
        uuid id PK
        string email UK
        string display_name
        string password_hash
        boolean is_active
        int role_id FK
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
        uuid family_id
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
        string storage_key UK
        string file_type
        string content_type
        bigint file_size
        string checksum_sha256
        string status
        text error_message
        datetime created_at
        datetime updated_at
    }

    DocumentSection {
        uuid id PK
        uuid document_id FK
        int ordinal
        string kind
        int page_number
        text heading
        smallint heading_level
        text text
        int char_count
        datetime created_at
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

Accounts. Soft-deleted via `deleted_at` (rows are retained for audit; the email stays reserved).

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | UUID | PRIMARY KEY, DEFAULT gen_random_uuid() | Unique identifier |
| email | VARCHAR(255) | UNIQUE, NOT NULL, CHECK (email = lower(email)) | Normalised (trimmed, lowercased) address |
| display_name | VARCHAR(100) | NOT NULL, CHECK (1-100 characters) | Name shown in the UI; not unique |
| password_hash | VARCHAR(255) | NOT NULL | bcrypt hash (never the password) |
| is_active | BOOLEAN | NOT NULL, DEFAULT true | Deactivated accounts cannot authenticate |
| role_id | INTEGER | FOREIGN KEY roles(id) ON DELETE RESTRICT, NOT NULL | The user's role |
| deleted_at | TIMESTAMPTZ | NULLABLE | Soft-delete timestamp |
| created_at | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | Creation timestamp |
| updated_at | TIMESTAMPTZ | NOT NULL, DEFAULT NOW(), refreshed by the ORM on update | Last update |

**Indexes**: `uq_users_email` (unique; serves login lookups), `ix_users_role_id` (foreign key).

**Design notes**: There is no `is_superuser` flag; the role is the single source of truth for
privilege. There is no unique `username`; the email is the login identifier and `display_name`
is only presentation (see DEC-014).

#### roles

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY (identity) | Identifier |
| name | VARCHAR(50) | UNIQUE, NOT NULL | `user` or `admin` |
| description | TEXT | NULLABLE | Description |
| created_at | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | Creation timestamp |

Seeded by migration `0002` with `user` and `admin`.

#### refresh_tokens

One row per issued refresh token. Tokens are opaque random values; only their SHA-256 digest is stored.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | UUID | PRIMARY KEY, DEFAULT gen_random_uuid() | Identifier |
| user_id | UUID | FOREIGN KEY users(id) ON DELETE CASCADE, NOT NULL | Owner |
| family_id | UUID | NOT NULL | Shared by every token in one login session (rotation chain) |
| token_hash | VARCHAR(64) | UNIQUE, NOT NULL | Hex SHA-256 of the token |
| expires_at | TIMESTAMPTZ | NOT NULL, CHECK (expires_at > created_at) | Expiry (7 days by default) |
| revoked_at | TIMESTAMPTZ | NULLABLE | Set when rotated, logged out or revoked |
| created_at | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | Creation timestamp |

**Indexes**: `uq_refresh_tokens_token_hash`, `ix_refresh_tokens_user_id`,
`ix_refresh_tokens_family_id`, `ix_refresh_tokens_expires_at` (for cleanup).

**Cascade**: deleting a user deletes their refresh tokens; a role that is in use cannot be deleted.

### Documents & Collections

#### collections

A user's named grouping of documents. Deleting a collection never deletes its documents.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | UUID | PRIMARY KEY, DEFAULT gen_random_uuid() | Identifier |
| user_id | UUID | FOREIGN KEY users(id) ON DELETE CASCADE, NOT NULL | Owner |
| name | VARCHAR(255) | NOT NULL, CHECK (1-255 characters) | Normalised name (NFC, single spaces) |
| description | TEXT | NULLABLE | Optional description (max 2000 characters, enforced by the API) |
| created_at / updated_at | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | Timestamps |

**Indexes / constraints**: `ix_collections_user_id`; `uq_collections_user_name` unique on
`(user_id, lower(name))` (names are unique per owner, case-insensitively);
`uq_collections_id_user_id` unique on `(id, user_id)` (target of the same-owner foreign key below).

#### documents

Metadata for an uploaded file. The file itself lives in object storage, never in PostgreSQL.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | UUID | PRIMARY KEY, DEFAULT gen_random_uuid() | Identifier |
| user_id | UUID | FOREIGN KEY users(id) ON DELETE CASCADE, NOT NULL | Owner (always the authenticated uploader) |
| filename | VARCHAR(255) | NOT NULL, CHECK (1-255 characters) | Sanitised display name; never used as a path |
| storage_key | VARCHAR(255) | UNIQUE, NOT NULL | Opaque generated key, e.g. `documents/3f/3fa9...` |
| file_type | VARCHAR(10) | NOT NULL, CHECK IN ('pdf','docx','txt','md') | Type determined from validated content |
| content_type | VARCHAR(127) | NOT NULL | Canonical media type chosen by the server |
| file_size | BIGINT | NOT NULL, CHECK (> 0) | Size in bytes |
| checksum_sha256 | VARCHAR(64) | NOT NULL | Hex SHA-256 of the content |
| status | VARCHAR(20) | NOT NULL, DEFAULT 'pending', CHECK (valid status) | Lifecycle state |
| error_message | TEXT | NULLABLE, CHECK (only when status = 'failed') | Safe, fixed human-readable failure message |
| failure_reason | VARCHAR(50) | NULLABLE, CHECK (only when status = 'failed') | Machine-readable failure code |
| page_count | INTEGER | NULLABLE, CHECK (>= 0) | Pages (PDF only; otherwise NULL) |
| character_count | BIGINT | NULLABLE, CHECK (>= 0) | Characters of normalised text |
| processing_started_at | TIMESTAMPTZ | NULLABLE | Start of the latest attempt |
| processing_completed_at | TIMESTAMPTZ | NULLABLE | When the latest attempt ended (ready or failed) |
| processing_attempts | INTEGER | NOT NULL, DEFAULT 0, CHECK (>= 0) | Attempts in the current retry budget |
| chunk_count | INTEGER | NULLABLE, CHECK (NULL or >= 0) | Chunks of the current run; NULL until chunked and again after a failure |
| chunking_version | VARCHAR(20) | NULLABLE | Chunking algorithm version that produced the chunks |
| processing_metadata | JSONB | NOT NULL, DEFAULT '{}' | Extractor, processing version, duration, script profile, sanitised file properties |
| created_at / updated_at | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | Timestamps |

**Status values** (see the lifecycle in the architecture document): `pending`, `parsing`, `chunking`,
`chunked`, `embedding`, `indexing`, `ready`, `failed`. `chunked` means chunks are stored and the document awaits
embedding; `ready` is reserved for embedded, indexed (searchable) documents.

**Indexes / constraints**: `ix_documents_user_created` on `(user_id, created_at DESC, id DESC)` (the
listing query); `ix_documents_status`; `uq_documents_storage_key`;
`uq_documents_user_checksum` unique on `(user_id, checksum_sha256)` (identical content is stored once per user);
`uq_documents_id_user_id` unique on `(id, user_id)`.

#### document_sections

The normalised text of a document, split at its natural boundaries. This is the input to the chunking phase.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | UUID | PRIMARY KEY, DEFAULT gen_random_uuid() | Identifier |
| document_id | UUID | FOREIGN KEY documents(id) ON DELETE CASCADE, NOT NULL | Owning document |
| ordinal | INTEGER | NOT NULL, CHECK (>= 0), UNIQUE with document_id | Reading order |
| kind | VARCHAR(10) | NOT NULL, CHECK IN ('page','section','body') | Source structure |
| page_number | INTEGER | NULLABLE, CHECK (>= 1) | 1-based page; set exactly when `kind = 'page'` |
| heading | TEXT | NULLABLE | Section heading text |
| heading_level | SMALLINT | NULLABLE, CHECK (1-9) | Heading level |
| text | TEXT | NOT NULL | Normalised text (may be empty for blank pages) |
| char_count | INTEGER | NOT NULL, CHECK (= char_length(text)) | Characters in `text` |
| created_at | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | Creation timestamp |

**Indexes**: `uq_document_sections_document_ordinal`, `ix_document_sections_document_page` on `(document_id, page_number)`.

`uq_document_sections_id_document` unique on `(id, document_id)` is the target of the chunk foreign key below.

#### document_chunks

Retrievable passages. Each chunk is an exact slice of one section and belongs to one document.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | UUID | PRIMARY KEY, DEFAULT gen_random_uuid() | Identifier |
| document_id | UUID | FOREIGN KEY documents(id) ON DELETE CASCADE, NOT NULL | Owning document |
| section_id | UUID | NOT NULL | Source section; `(section_id, document_id)` is a composite foreign key to `document_sections(id, document_id)` ON DELETE CASCADE, so a chunk can never point at another document's section |
| chunking_version | VARCHAR(20) | NOT NULL | Algorithm version, for example `c1.0` |
| chunk_index | INTEGER | NOT NULL, CHECK (>= 0) | Reading order within the document |
| text | TEXT | NOT NULL | Exactly `section.text[start_char:end_char]` |
| char_count | INTEGER | NOT NULL, CHECK (= char_length(text) AND > 0) | Characters in `text` |
| text_sha256 | VARCHAR(64) | NOT NULL | Hex SHA-256 of `text` (change detection, deduplication) |
| start_char / end_char | INTEGER | NOT NULL, CHECK (end > start >= 0 AND end - start = char_count) | Offsets within the section text |
| overlap_chars | INTEGER | NOT NULL, DEFAULT 0, CHECK (0 <= x <= char_count) | Leading characters shared with the previous chunk |
| page_number | INTEGER | NULLABLE, CHECK (>= 1) | Source page (PDF) |
| heading / heading_level | TEXT / SMALLINT | NULLABLE, CHECK (1-9) | Heading of the source section |
| heading_path | TEXT[] | NOT NULL, DEFAULT '{}' | Enclosing headings, outermost first |
| created_at | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | Creation timestamp |

**Indexes / constraints**: `uq_document_chunks_order` unique on `(document_id, chunking_version, chunk_index)`
(idempotency and ordering), `ix_document_chunks_section_id`, `ix_document_chunks_document_page` on `(document_id, page_number)`.
Chunk text duplicates section text (plus overlap) by design: chunks are what retrieval reads, and the exact-slice
rule keeps the two verifiable. A later phase adds the embedding column here.

#### document_collections

Many-to-many membership. The composite foreign keys make it **impossible at the database level**
to link a document to a collection owned by a different user.

| Column | Type | Constraints |
|--------|------|-------------|
| document_id | UUID | PRIMARY KEY (with collection_id) |
| collection_id | UUID | PRIMARY KEY (with document_id) |
| user_id | UUID | NOT NULL |
| created_at | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() |

`(document_id, user_id)` references `documents(id, user_id)` and `(collection_id, user_id)` references
`collections(id, user_id)`, both `ON DELETE CASCADE`. Index: `ix_document_collections_collection_id`.

**Deletion behaviour**: deleting a document or a collection removes only the link rows; deleting a user
cascades to their documents, collections and links.

#### document_versions (deferred to Phase 10)

Version history is designed but not yet created, so Phase 3 has no unused tables. The Phase 10 migration will
add `document_versions` and move per-file storage details there. Likewise `page_count`, `word_count` and
`metadata` are added by the Phase 4 processing migration.

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
