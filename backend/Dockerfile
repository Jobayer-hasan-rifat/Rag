FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN groupadd --system app && useradd --system --gid app --create-home app
WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

# Development image: adds test and lint tooling; source is bind-mounted by Compose.
FROM base AS dev
COPY requirements-dev.txt ./
RUN pip install -r requirements-dev.txt
COPY . .
RUN chown -R app:app /app
USER app
CMD ["uvicorn", "app.main:get_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# Production-oriented image: runtime dependencies only, non-root.
FROM base AS prod
COPY . .
RUN chown -R app:app /app
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:get_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
