PYTHON ?= python

.PHONY: install lint format format-check typecheck test test-unit test-integration migrate lock

install:
	$(PYTHON) -m pip install -r requirements-dev.txt

lint:
	$(PYTHON) -m ruff check .

format:
	$(PYTHON) -m black .
	$(PYTHON) -m ruff check . --fix

format-check:
	$(PYTHON) -m black --check .

typecheck:
	$(PYTHON) -m mypy

test:
	$(PYTHON) -m pytest

test-unit:
	$(PYTHON) -m pytest -m "not integration"

test-integration:
	$(PYTHON) -m pytest -m integration

migrate:
	$(PYTHON) -m alembic upgrade head

lock:
	uv pip compile pyproject.toml -o requirements.txt --universal --python-version 3.12 --no-header
	uv pip compile pyproject.toml --extra dev -o requirements-dev.txt --universal --python-version 3.12 --no-header
