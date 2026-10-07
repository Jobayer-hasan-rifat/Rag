# Contributing Guide

Thank you for your interest in contributing to the Intelligent Document Processing & RAG Platform. This document provides guidelines and standards for contributing to this project.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Development Philosophy](#development-philosophy)
- [Development Setup](#development-setup)
- [Branch Strategy](#branch-strategy)
- [Commit Conventions](#commit-conventions)
- [Testing Requirements](#testing-requirements)
- [Code Quality Standards](#code-quality-standards)
- [Pull Request Process](#pull-request-process)
- [Documentation Standards](#documentation-standards)

## Code of Conduct

### Our Pledge

In the interest of fostering an open and welcoming environment, we pledge to make participation in our project a harassment-free experience for everyone, regardless of experience level, gender, gender identity and expression, sexual orientation, disability, personal appearance, body size, race, ethnicity, age, religion, or nationality.

### Our Standards

**Positive behavior includes:**
- Using welcoming and inclusive language
- Being respectful of differing viewpoints
- Gracefully accepting constructive criticism
- Focusing on what is best for the community
- Showing empathy towards other community members

**Unacceptable behavior includes:**
- Trolling, insulting comments, and personal attacks
- Public or private harassment
- Publishing others' private information without permission
- Other conduct which could reasonably be considered inappropriate

## Development Philosophy

### Engineering Principles

1. **Quality Over Speed**: We prefer well-tested, well-documented code over quick fixes
2. **Security First**: Security is never an afterthought
3. **Simplicity**: Choose simple solutions over clever ones
4. **Testability**: All code should be testable and tested
5. **Maintainability**: Write code for the next developer
6. **Documentation**: Code should be self-documenting with clear comments where needed

### What We Value

- **Correctness**: Does it work as intended?
- **Security**: Is it secure?
- **Performance**: Is it fast enough?
- **Maintainability**: Can it be easily modified?
- **Testability**: Can it be easily tested?
- **Readability**: Can it be easily understood?

## Development Setup

### Prerequisites

- Python 3.12+
- Node.js 18+ (for frontend)
- Docker Desktop
- PostgreSQL 16 (via Docker)
- Redis (via Docker)
- Git

### Initial Setup

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd rag-platform
   ```

2. **Set up environment variables**
   ```bash
   cp .env.example .env
   # Edit .env with your local configuration
   ```

3. **Start infrastructure services**
   ```bash
   docker-compose up -d postgres redis
   ```

4. **Set up Python environment**
   ```bash
   cd backend
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install -r requirements.txt
   pip install -r requirements-dev.txt
   ```

5. **Run database migrations**
   ```bash
   alembic upgrade head
   ```

6. **Run the development server**
   ```bash
   uvicorn app.main:app --reload
   ```

7. **Set up frontend (in a new terminal)**
   ```bash
   cd frontend
   npm install
   npm run dev
   ```

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=app --cov-report=html

# Run specific test types
pytest tests/unit/           # Unit tests only
pytest tests/integration/    # Integration tests only
pytest tests/e2e/            # End-to-end tests only

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/unit/test_auth.py

# Run specific test
pytest tests/unit/test_auth.py::test_user_registration
```

### Code Quality Tools

```bash
# Run linter
ruff check .

# Format code
black .

# Type checking
mypy app

# Run all quality checks
ruff check . && black --check . && mypy app
```

## Branch Strategy

### Branch Naming

- `main` - Production-ready code
- `develop` - Integration branch for features
- `feature/phase-N-description` - Feature development
- `fix/description` - Bug fixes
- `refactor/description` - Code refactoring
- `docs/description` - Documentation updates
- `security/description` - Security fixes
- `perf/description` - Performance improvements

Examples:
- `feature/phase-2-authentication`
- `fix/document-upload-validation`
- `docs/api-documentation-update`

### Branch Workflow

1. **Create branch from `develop`**
   ```bash
   git checkout develop
   git pull origin develop
   git checkout -b feature/phase-2-authentication
   ```

2. **Make changes and commit**
   ```bash
   git add .
   git commit -m "feat(auth): implement user registration"
   ```

3. **Push branch to remote**
   ```bash
   git push -u origin feature/phase-2-authentication
   ```

4. **Create pull request to `develop`**

5. **After review and approval, merge to `develop`**

6. **When release-ready, merge `develop` to `main`**

### Branch Protection Rules

- `main` branch:
  - No direct commits
  - Requires pull request
  - Requires at least 1 approval
  - Requires status checks to pass

- `develop` branch:
  - No direct commits
  - Requires pull request
  - Requires status checks to pass

## Commit Conventions

### Conventional Commits

We follow the [Conventional Commits](https://www.conventionalcommits.org/) specification.

Format:
```
<type>(<scope>): <subject>

<body>

<footer>
```

### Commit Types

| Type | Description | Example |
|------|-------------|---------|
| `feat` | New feature | `feat(auth): implement JWT authentication` |
| `fix` | Bug fix | `fix(upload): correct file size validation` |
| `refactor` | Code refactor | `refactor(search): extract hybrid search logic` |
| `test` | Adding/updating tests | `test(auth): add registration tests` |
| `docs` | Documentation only | `docs(api): update endpoint documentation` |
| `perf` | Performance improvement | `perf(embedding): batch processing optimization` |
| `security` | Security fix | `security(auth): fix token validation vulnerability` |
| `chore` | Maintenance | `chore(deps): update dependencies` |
| `style` | Code style (formatting) | `style: apply black formatting` |

### Commit Scopes

Common scopes:
- `auth` - Authentication
- `docs` - Document management
- `search` - Search functionality
- `rag` - RAG functionality
- `db` - Database
- `api` - API endpoints
- `worker` - Background workers
- `config` - Configuration
- `deps` - Dependencies

### Commit Message Guidelines

**Good commit messages:**
```
feat(auth): implement user registration endpoint

- Add /auth/register endpoint
- Implement password hashing with bcrypt
- Add input validation with Pydantic
- Write unit tests for registration

Closes #123
```

```
fix(search): correct vector similarity search

The vector search was returning results with low similarity
scores. This fix adds a minimum similarity threshold.

Fixes #456
```

**Bad commit messages:**
```
fixed stuff
```
```
updates
```
```
WIP
```

### Commit Rules

- **Atomic commits**: Each commit should represent one logical change
- **Small commits**: Prefer many small commits over one large commit
- **Meaningful messages**: Write messages that explain the "why"
- **Reference issues**: Link to relevant issues in footer
- **No secrets**: Never commit sensitive information

## Testing Requirements

### Test Coverage

- **Minimum coverage**: 80% for core business logic
- **Security-critical code**: 100% coverage required
- **New features**: Must include tests
- **Bug fixes**: Must include regression tests

### Test Types

#### Unit Tests
- Test individual functions/methods in isolation
- Mock external dependencies
- Fast execution (< 1 second per test)
- Located in `tests/unit/`

#### Integration Tests
- Test component interactions
- Use real database (test database)
- May use testcontainers
- Located in `tests/integration/`

#### End-to-End Tests
- Test complete user flows
- Use full application stack
- Located in `tests/e2e/`

### Test Naming

Pattern: `test_<scenario>_<expected_result>`

Examples:
```python
def test_user_registration_with_valid_data_succeeds():
    pass

def test_user_registration_with_invalid_email_fails():
    pass

def test_login_with_correct_credentials_returns_tokens():
    pass

def test_login_with_incorrect_password_fails():
    pass
```

### Test Structure

Follow Arrange-Act-Assert pattern:

```python
def test_document_upload_with_valid_pdf_succeeds(client, auth_headers):
    # Arrange
    file_content = create_test_pdf()
    files = {"file": ("test.pdf", file_content, "application/pdf")}
    
    # Act
    response = client.post(
        "/api/v1/documents",
        files=files,
        headers=auth_headers
    )
    
    # Assert
    assert response.status_code == 201
    assert response.json()["filename"] == "test.pdf"
```

### Test Data

Use factories for test data:

```python
from factory import Factory, Faker, SubFactory
from app.models import User

class UserFactory(Factory):
    class Meta:
        model = User
    
    email = Faker("email")
    username = Faker("user_name")
    hashed_password = "hashed_password"
```

### Running Tests

Tests must pass before any pull request can be merged:

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=app --cov-report=html --cov-fail-under=80

# Run in parallel (faster)
pytest -n auto
```

## Code Quality Standards

### Linting (Ruff)

We use Ruff for linting. Configuration in `pyproject.toml`:

```toml
[tool.ruff]
line-length = 100
target-version = "py312"

select = [
    "E",   # pycodestyle errors
    "W",   # pycodestyle warnings
    "F",   # pyflakes
    "I",   # isort
    "B",   # flake8-bugbear
    "C4",  # flake8-comprehensions
    "UP",  # pyupgrade
]
```

Run linting:
```bash
ruff check .
ruff check . --fix  # Auto-fix issues
```

### Formatting (Black)

Code must be formatted with Black:

```bash
black .
black --check .  # Check without modifying
```

Configuration in `pyproject.toml`:
```toml
[tool.black]
line-length = 100
target-version = ['py312']
```

### Type Checking (mypy)

Static type checking with mypy:

```bash
mypy app
```

Configuration in `pyproject.toml`:
```toml
[tool.mypy]
python_version = "3.12"
strict = true
warn_return_any = true
warn_unused_configs = true
```

### Import Order

Imports should be ordered:
1. Standard library
2. Third-party packages
3. Local application imports

```python
# Standard library
import os
from typing import Optional

# Third-party
from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session

# Local
from app.config import settings
from app.models import User
```

## Pull Request Process

### Before Creating PR

1. **Update from develop**
   ```bash
   git checkout develop
   git pull origin develop
   git checkout your-branch
   git merge develop
   ```

2. **Run all checks**
   ```bash
   # Run tests
   pytest
   
   # Run linting
   ruff check .
   
   # Run formatting check
   black --check .
   
   # Run type checking
   mypy app
   ```

3. **Update documentation** if needed

### PR Template

```markdown
## Description
Brief description of changes

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update

## Testing
- [ ] Tests added/updated
- [ ] All tests pass
- [ ] Coverage maintained/improved

## Checklist
- [ ] Code follows style guidelines
- [ ] Self-review completed
- [ ] Documentation updated
- [ ] No new warnings
- [ ] Tests added for new functionality
- [ ] Security considerations addressed
```

### PR Review Process

1. **Automated checks must pass**
   - Tests pass
   - Linting passes
   - Type checking passes
   - Coverage threshold met

2. **Code review**
   - At least one approval required for `develop`
   - Two approvals required for `main`
   - Address all review comments

3. **Merge**
   - Squash and merge for feature branches
   - Merge commit for `develop` to `main`

### After Merge

1. **Delete feature branch**
   ```bash
   git checkout develop
   git pull origin develop
   git branch -d your-feature-branch
   git push origin --delete your-feature-branch
   ```

2. **Update local develop**
   ```bash
   git pull origin develop
   ```

## Documentation Standards

### Code Documentation

#### Module Docstrings
```python
"""
Document processing service.

This module provides functionality for parsing, chunking,
and indexing documents for retrieval.
"""
```

#### Function/Method Docstrings
```python
def process_document(document_id: int, db: Session) -> ProcessingResult:
    """
    Process a document through the ingestion pipeline.
    
    Args:
        document_id: The ID of the document to process
        db: Database session
        
    Returns:
        ProcessingResult with status and metadata
        
    Raises:
        DocumentNotFoundError: If document doesn't exist
        ProcessingError: If processing fails
    """
    pass
```

#### Class Docstrings
```python
class DocumentProcessor:
    """
    Handles document processing pipeline.
    
    Attributes:
        chunk_size: Maximum characters per chunk
        chunk_overlap: Overlap between chunks
        
    Example:
        >>> processor = DocumentProcessor(chunk_size=500)
        >>> result = processor.process(pdf_content)
    """
    pass
```

### README Documentation

- Keep README up-to-date
- Include setup instructions
- Include usage examples
- Link to detailed documentation

### API Documentation

- Use OpenAPI/Swagger for API docs
- Document all endpoints
- Include request/response examples
- Document error responses

### Architecture Documentation

- Update docs when architecture changes
- Keep diagrams up-to-date
- Document decision rationale

## Questions or Issues?

- Open an issue for bugs or feature requests
- Use discussions for questions
- Check existing issues before creating new ones

---

Thank you for contributing! 🎉
