# Testing Strategy

## Overview

This document defines the comprehensive testing strategy for the Intelligent Document Processing & RAG Platform, covering unit tests, integration tests, E2E tests, and AI/RAG evaluation tests.

## Testing Pyramid

```mermaid
graph TB
    E2E[E2E Tests<br/>Few]
    Integration[Integration Tests<br/>Some]
    Unit[Unit Tests<br/>Many]
    
    Unit --> Integration
    Integration --> E2E
    
    style Unit fill:#e1f5ff
    style Integration fill:#fff4e1
    style E2E fill:#ffe1f5
```

**Principle**: More unit tests (fast, isolated), fewer E2E tests (slow, expensive).

## Test Types

### Unit Tests

**Purpose**: Test individual functions/methods in isolation.

**Characteristics**:
- Fast execution (< 1 second per test)
- No external dependencies (mocked)
- Test one thing per test
- Clear Arrange-Act-Assert structure

**What to Test**:
- Service methods
- Utility functions
- Data transformations
- Validation logic
- Business rules

**What to Mock**:
- Database calls
- External API calls
- File system operations
- Time-dependent code

**Example**:
```python
# tests/unit/services/test_document_service.py
import pytest
from unittest.mock import Mock, AsyncMock
from app.services.document_service import DocumentService
from app.models import Document

@pytest.mark.asyncio
async def test_upload_document_validates_file():
    # Arrange
    mock_repo = Mock()
    mock_storage = Mock()
    service = DocumentService(mock_repo, mock_storage)
    
    file = Mock()
    file.filename = "test.pdf"
    file.content_type = "application/pdf"
    
    user = Mock(id=UUID("12345678-1234-5678-1234-567812345678"))
    
    # Act
    result = await service.upload_document(file, user.id)
    
    # Assert
    assert result.filename == "test.pdf"
    assert result.status == "pending"
    mock_repo.create.assert_called_once()
```

### Integration Tests

**Purpose**: Test component interactions with real dependencies.

**Characteristics**:
- Slower execution (seconds to minutes)
- Real database (test database)
- Real file system (temp directories)
- Real external services (or testcontainers)

**What to Test**:
- API endpoints
- Database operations
- Service integrations
- Message queue interactions

**Test Database**:
```python
# tests/conftest.py
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from app.models.base import Base

TEST_DATABASE_URL = "postgresql+asyncpg://test:test@localhost:5432/test_db"

@pytest.fixture(scope="function")
async def db_session():
    engine = create_async_engine(TEST_DATABASE_URL, echo=True)
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    
    async with async_session() as session:
        yield session
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    
    await engine.dispose()
```

**Example**:
```python
# tests/integration/test_documents_api.py
import pytest
from httpx import AsyncClient
from app.main import app

@pytest.mark.asyncio
async def test_upload_document_creates_record(authenticated_client: AsyncClient):
    # Arrange
    file_content = b"Test PDF content"
    files = {"file": ("test.pdf", file_content, "application/pdf")}
    
    # Act
    response = await authenticated_client.post(
        "/api/v1/documents",
        files=files
    )
    
    # Assert
    assert response.status_code == 201
    data = response.json()["data"]
    assert data["filename"] == "test.pdf"
    assert data["status"] == "pending"
    
    # Verify in database
    doc = await db.get(Document, data["id"])
    assert doc is not None
    assert doc.filename == "test.pdf"
```

### End-to-End Tests

**Purpose**: Test complete user flows.

**Characteristics**:
- Slow execution (minutes)
- Full system running
- Real browser (for frontend)
- Real API server

**What to Test**:
- User registration and login flow
- Document upload and processing flow
- Search and RAG query flow
- Complete user journeys

**Example**:
```python
# tests/e2e/test_user_journey.py
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_complete_document_workflow():
    async with AsyncClient(base_url="http://localhost:8000") as client:
        # 1. Register
        response = await client.post("/api/v1/auth/register", json={
            "email": "test@example.com",
            "display_name": "Test User",
            "password": "TestPass123!"
        })
        assert response.status_code == 201
        
        # 2. Login
        response = await client.post("/api/v1/auth/login", json={
            "email": "test@example.com",
            "password": "TestPass123!"
        })
        assert response.status_code == 200
        token = response.json()["data"]["access_token"]
        
        headers = {"Authorization": f"Bearer {token}"}
        
        # 3. Upload document
        files = {"file": ("test.txt", b"Test content", "text/plain")}
        response = await client.post(
            "/api/v1/documents",
            files=files,
            headers=headers
        )
        assert response.status_code == 201
        doc_id = response.json()["data"]["id"]
        
        # 4. Wait for processing (with timeout)
        for _ in range(30):
            response = await client.get(
                f"/api/v1/documents/{doc_id}",
                headers=headers
            )
            if response.json()["data"]["status"] == "ready":
                break
            await asyncio.sleep(1)
        
        # 5. Search
        response = await client.post(
            "/api/v1/search/semantic",
            json={"query": "test", "top_k": 5},
            headers=headers
        )
        assert response.status_code == 200
        assert len(response.json()["data"]["results"]) > 0
```

## Test Organization

### Directory Structure

```
backend/tests/
├── conftest.py              # Shared fixtures
├── factories.py             # Test data factories
│
├── unit/
│   ├── test_auth_service.py
│   ├── test_document_service.py
│   ├── test_search_service.py
│   ├── test_chunking.py
│   └── test_parsers/
│       ├── test_pdf_parser.py
│       └── test_docx_parser.py
│
├── integration/
│   ├── test_auth_api.py
│   ├── test_documents_api.py
│   ├── test_search_api.py
│   ├── test_rag_api.py
│   └── test_database.py
│
└── e2e/
    ├── test_user_journey.py
    └── test_document_workflow.py
```

### Test Naming Convention

Pattern: `test_<scenario>_<expected_result>`

```python
def test_user_registration_with_valid_data_succeeds():
    pass

def test_user_registration_with_duplicate_email_fails():
    pass

def test_document_upload_with_oversized_file_returns_413():
    pass
```

## Test Fixtures

### Shared Fixtures

```python
# tests/conftest.py
import pytest
from httpx import AsyncClient
from app.main import app

@pytest.fixture
async def client():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        yield ac

@pytest.fixture
async def authenticated_client(client: AsyncClient, test_user):
    # Login and get token
    response = await client.post("/api/v1/auth/login", json={
        "email": test_user.email,
        "password": "TestPass123!"
    })
    token = response.json()["data"]["access_token"]
    
    client.headers["Authorization"] = f"Bearer {token}"
    yield client

@pytest.fixture
async def test_user(db_session):
    from app.services.auth_service import AuthService
    
    user = await AuthService.register_user(
        email="test@example.com",
        display_name="Test User",
        password="TestPass123!",
        db=db_session
    )
    return user

@pytest.fixture
async def test_document(db_session, test_user):
    from app.models import Document
    
    doc = Document(
        id=UUID("12345678-1234-5678-1234-567812345678"),
        user_id=test_user.id,
        filename="test.pdf",
        storage_path="/uploads/test.pdf",
        file_type="pdf",
        file_size=1024,
        status="ready"
    )
    db_session.add(doc)
    await db_session.commit()
    return doc
```

### Test Data Factories

```python
# tests/factories.py
from factory import Factory, Faker, SubFactory
from app.models import User, Document

class UserFactory(Factory):
    class Meta:
        model = User
    
    id = Faker("uuid4")
    email = Faker("email")
    display_name = Faker("name")
    password_hash = "hashed_password_hash"
    is_active = True
    role_id = 1

class DocumentFactory(Factory):
    class Meta:
        model = Document
    
    id = Faker("uuid4")
    user = SubFactory(UserFactory)
    filename = Faker("file_name", extension="pdf")
    storage_path = "/uploads/test.pdf"
    file_type = "pdf"
    file_size = Faker("random_int", min=1024, max=10485760)
    status = "ready"
```

## What to Test and What to Mock

### Test for Real

- Business logic in services
- Data validation in schemas
- Utility functions
- API request/response handling
- Database queries (in integration tests)

### Mock

- External API calls (LLM providers, embedding providers)
- File system operations (in unit tests)
- Time-dependent code
- Network calls (in unit tests)

### Balance

- Integration tests: Use real database, real file system
- Unit tests: Mock database, mock file system

## Test Coverage

### Coverage Requirements

- **Minimum**: 80% coverage for core business logic
- **Security-critical code**: 100% coverage
- **API endpoints**: 100% endpoint coverage
- **New features**: Must include tests

### Running Coverage

```bash
# Run with coverage
pytest --cov=app --cov-report=html --cov-fail-under=80

# View report
open htmlcov/index.html
```

### Coverage Configuration

```ini
# pyproject.toml
[tool.coverage.run]
source = ["app"]
omit = [
    "app/main.py",
    "app/config.py",
    "*/tests/*",
]

[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "def __repr__",
    "raise AssertionError",
    "raise NotImplementedError",
    "if __name__ == .__main__.:",
]
```

## AI/RAG Evaluation Tests

### Purpose

Test retrieval and generation quality, not just code correctness.

### Retrieval Evaluation

```python
# tests/evaluation/test_retrieval.py
import pytest

@pytest.mark.asyncio
async def test_retrieval_recall_at_k():
    """
    Test that retrieval finds relevant documents.
    """
    # Setup: Upload known documents with known relevant chunks
    # ...
    
    # Query
    query = "What is machine learning?"
    results = await search_service.semantic_search(query, top_k=10)
    
    # Evaluate
    relevant_chunk_ids = ["chunk1", "chunk2", "chunk3"]
    retrieved_ids = [r.chunk_id for r in results]
    
    # Calculate Recall@K
    hits = len(set(relevant_chunk_ids) & set(retrieved_ids))
    recall = hits / len(relevant_chunk_ids)
    
    assert recall >= 0.8  # Should find 80% of relevant chunks
```

### Answer Quality Evaluation

```python
@pytest.mark.asyncio
async def test_answer_groundedness():
    """
    Test that generated answers are grounded in context.
    """
    # Query
    query = "What are the types of machine learning?"
    response = await rag_service.query(query)
    
    # Check citations
    assert len(response.citations) > 0
    
    # Check answer content exists
    assert len(response.answer) > 50
    
    # Check answer relevance (manual or LLM-based evaluation)
    # ...
```

## Performance Testing

### Load Testing (Future)

Use Locust or k6 for load testing:

```python
# tests/performance/locustfile.py
from locust import HttpUser, task, between

class DocumentUser(HttpUser):
    wait_time = between(1, 3)
    
    @task
    def upload_document(self):
        files = {"file": ("test.pdf", b"content", "application/pdf")}
        self.client.post("/api/v1/documents", files=files)
    
    @task
    def search(self):
        self.client.post("/api/v1/search/semantic", json={
            "query": "test query",
            "top_k": 10
        })
```

## Test Execution

### Running Tests

```bash
# Run all tests
pytest

# Run specific test types
pytest tests/unit/
pytest tests/integration/
pytest tests/e2e/

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/unit/test_auth_service.py

# Run specific test
pytest tests/unit/test_auth_service.py::test_user_registration_with_valid_data_succeeds

# Run with parallel execution
pytest -n auto

# Run with coverage
pytest --cov=app --cov-report=html
```

### Test Configuration

Configuration lives in `backend/pyproject.toml` (`asyncio_mode = auto`, strict markers).
Tests marked `integration` need real PostgreSQL/Redis.

**Infrastructure approach (decided in Phase 1)**:

- Locally, session-scoped fixtures start `pgvector/pgvector:pg16` and `redis:7-alpine` with
  Testcontainers. Docker is the only requirement; nothing depends on the developer's own
  database or Redis.
- In CI, the same tests use service containers by setting `TEST_DATABASE_URL` and
  `TEST_REDIS_URL`, which the fixtures prefer over starting containers.
- Migration tests create a fresh database per test and exercise `upgrade head`,
  `downgrade -1` and a second `upgrade head`.
- The Celery test starts a real worker thread against real Redis.
- PostgreSQL and Redis are never mocked.

**Phase 2 results (2026-10-08)**: 277 tests (unit, API, integration) passing, about 99% line
coverage of `app/`; all authentication and security modules are fully covered. Registration, login, refresh
rotation and reuse detection, logout revocation, RBAC and rate limiting are tested end to end through the HTTP API
against real PostgreSQL and Redis. Migration tests cover upgrade, downgrade, re-upgrade, constraints, cascades
and model/migration drift. The only mocks are narrow Redis-outage simulations. Coverage is measured with
`concurrency = [thread, greenlet]` because SQLAlchemy's async layer runs on greenlets.

## CI/CD Integration

### GitHub Actions (Future)

```yaml
# .github/workflows/test.yml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_PASSWORD: test
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
      
      redis:
        image: redis:7
        options: >-
          --health-cmd "redis-cli ping"
          --health-interval 10s
    
    steps:
      - uses: actions/checkout@v3
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.12'
      
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          pip install -r requirements-dev.txt
      
      - name: Run linting
        run: ruff check .
      
      - name: Run formatting check
        run: black --check .
      
      - name: Run type checking
        run: mypy app
      
      - name: Run tests
        run: pytest --cov=app --cov-fail-under=80
        env:
          DATABASE_URL: postgresql+asyncpg://postgres:test@localhost:5432/test
          REDIS_URL: redis://localhost:6379/0
```

## Test Quality Gates

### Before Merging

All tests must pass:
- [ ] Unit tests pass
- [ ] Integration tests pass
- [ ] E2E tests pass
- [ ] Coverage ≥ 80%
- [ ] No flaky tests
- [ ] Linting passes
- [ ] Type checking passes

---

*Testing is not optional. Tests verify behavior, not just code coverage.*
