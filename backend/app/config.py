import re
from functools import lru_cache
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.embeddings.constants import VECTOR_DIMENSION

_COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
_PLACEHOLDER_MARKERS = ("change_this", "changeme", "your-secret")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "RAG-Platform"
    app_env: Literal["development", "test", "staging", "production"] = "development"
    app_debug: bool = False

    database_url: str
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_max_overflow: int = Field(default=20, ge=0, le=200)

    redis_url: str

    celery_broker_url: str
    celery_result_backend: str

    jwt_secret_key: SecretStr
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    jwt_issuer: str = "rag-platform"
    jwt_audience: str = "rag-platform-users"
    access_token_expire_minutes: int = Field(default=15, ge=1, le=60)
    refresh_token_expire_days: int = Field(default=7, ge=1, le=90)
    bcrypt_cost_factor: int = Field(default=12, ge=4, le=16)

    storage_backend: Literal["local"] = "local"
    storage_local_path: str = Field(default="./data/uploads", min_length=1)
    max_upload_bytes: int = Field(default=52_428_800, ge=1024, le=1_073_741_824)
    max_storage_bytes_per_user: int = Field(default=1_073_741_824, ge=1024)

    processing_max_attempts: int = Field(default=3, ge=1, le=10)
    processing_retry_backoff_seconds: int = Field(default=30, ge=0, le=3600)
    processing_timeout_seconds: int = Field(default=120, ge=2, le=3600)
    processing_max_pages: int = Field(default=2000, ge=1)
    processing_max_text_chars: int = Field(default=20_000_000, ge=1000)
    processing_max_docx_uncompressed_bytes: int = Field(default=200 * 1024 * 1024, ge=1024 * 1024)
    processing_stale_after_seconds: int = Field(default=300, ge=10)
    processing_sweep_interval_seconds: int = Field(default=60, ge=5)
    processing_pending_requeue_after_seconds: int = Field(default=120, ge=10)
    processing_sweep_batch_size: int = Field(default=100, ge=1, le=1000)
    chunking_max_chars: int = Field(default=1000, ge=100, le=20_000)
    chunking_overlap_chars: int = Field(default=150, ge=0)
    chunking_min_chars: int = Field(default=20, ge=1)
    chunking_max_chunks: int = Field(default=50_000, ge=1)
    embedding_provider: str = "sentence_transformers"
    embedding_model: str = "intfloat/multilingual-e5-small"
    embedding_model_revision: str = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
    embedding_dimension: int = Field(default=VECTOR_DIMENSION, ge=1, le=2000)
    embedding_normalize: bool = True
    embedding_query_prefix: str = "query: "
    embedding_passage_prefix: str = "passage: "
    embedding_max_seq_length: int = Field(default=512, ge=16, le=8192)
    embedding_include_heading: bool = False
    embedding_batch_size: int = Field(default=8, ge=1, le=256)
    embedding_device: Literal["auto", "cpu", "cuda", "mps"] = "auto"
    embedding_cache_dir: str | None = None
    embedding_allow_download: bool = True
    embedding_torch_threads: int | None = Field(default=None, ge=1, le=256)
    embedding_max_attempts: int = Field(default=3, ge=1, le=10)
    embedding_retry_backoff_seconds: int = Field(default=30, ge=0, le=3600)
    embedding_task_budget_seconds: int = Field(default=180, ge=5, le=3600)
    embedding_model_load_allowance_seconds: int = Field(default=120, ge=10, le=3600)
    embedding_stale_after_seconds: int = Field(default=600, ge=10)
    embedding_requeue_after_seconds: int = Field(default=120, ge=10)
    search_default_top_k: int = Field(default=10, ge=1, le=100)
    search_max_top_k: int = Field(default=50, ge=1, le=200)
    search_max_query_chars: int = Field(default=1000, ge=10, le=10_000)
    search_max_concurrent_queries: int = Field(default=4, ge=1, le=64)
    search_query_timeout_seconds: int = Field(default=20, ge=1, le=300)
    # None: exact search (the default; see docs). Set it only after adding an HNSW index.
    search_hnsw_ef_search: int | None = Field(default=None, ge=1, le=1000)
    # Hybrid search: candidates per retriever, Reciprocal Rank Fusion constant and weights
    hybrid_dense_candidates: int = Field(default=50, ge=1, le=200)
    hybrid_sparse_candidates: int = Field(default=50, ge=1, le=200)
    hybrid_rrf_k: int = Field(default=60, ge=1, le=1000)
    hybrid_dense_weight: float = Field(default=1.0, ge=0.0, le=10.0)
    hybrid_sparse_weight: float = Field(default=1.0, ge=0.0, le=10.0)
    sparse_max_terms: int = Field(default=12, ge=1, le=40)
    sparse_remove_stopwords: bool = True
    sparse_statement_timeout_ms: int = Field(default=2000, ge=100, le=60000)
    worker_max_tasks_per_child: int = Field(default=50, ge=1)
    worker_max_memory_per_child_kb: int = Field(default=1_000_000, ge=50_000)

    rate_limit_enabled: bool = True
    rate_limit_auth_attempts: int = Field(default=10, ge=1)
    rate_limit_window_seconds: int = Field(default=60, ge=1)
    rate_limit_upload_attempts: int = Field(default=20, ge=1)
    rate_limit_search_attempts: int = Field(default=60, ge=1)

    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    @field_validator(
        "embedding_cache_dir", "embedding_torch_threads", "search_hnsw_ef_search", mode="before"
    )
    @classmethod
    def _empty_means_unset(cls, value: object) -> object:
        """`FOO=` in an env file means "not set" for these optional settings."""
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("cors_origins")
    @classmethod
    def _reject_wildcard_origins(cls, origins: list[str]) -> list[str]:
        if any(origin == "*" for origin in origins):
            raise ValueError("CORS_ORIGINS must list explicit origins; '*' is not allowed")
        return origins

    @field_validator("database_url")
    @classmethod
    def _require_asyncpg(cls, url: str) -> str:
        if not url.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use the postgresql+asyncpg:// scheme")
        return url

    @field_validator("redis_url", "celery_broker_url", "celery_result_backend")
    @classmethod
    def _require_redis_scheme(cls, url: str) -> str:
        if not url.startswith(("redis://", "rediss://")):
            raise ValueError("Redis URLs must use the redis:// or rediss:// scheme")
        return url

    @field_validator("jwt_secret_key")
    @classmethod
    def _require_long_secret(cls, secret: SecretStr) -> SecretStr:
        if len(secret.get_secret_value()) < 32:
            raise ValueError("JWT_SECRET_KEY must be at least 32 characters")
        return secret

    @model_validator(mode="after")
    def _check_processing_timing(self) -> "Settings":
        if self.processing_stale_after_seconds <= self.processing_hard_time_limit_seconds:
            raise ValueError(
                "PROCESSING_STALE_AFTER_SECONDS must exceed the task hard time limit "
                "(PROCESSING_TIMEOUT_SECONDS + 45)"
            )
        return self

    @model_validator(mode="after")
    def _check_chunking_sizes(self) -> "Settings":
        if self.chunking_overlap_chars * 2 > self.chunking_max_chars:
            raise ValueError("CHUNKING_OVERLAP_CHARS must be at most half of CHUNKING_MAX_CHARS")
        if self.chunking_min_chars >= self.chunking_max_chars:
            raise ValueError("CHUNKING_MIN_CHARS must be smaller than CHUNKING_MAX_CHARS")
        return self

    @model_validator(mode="after")
    def _check_embedding_settings(self) -> "Settings":
        if self.embedding_dimension != VECTOR_DIMENSION:
            raise ValueError(
                f"EMBEDDING_DIMENSION must equal the database vector width ({VECTOR_DIMENSION}); "
                "a model with another dimension needs a migration first"
            )
        if self.embedding_provider == "sentence_transformers" and not (
            _COMMIT_SHA.fullmatch(self.embedding_model_revision)
            or PurePosixPath(self.embedding_model).is_absolute()
            or PureWindowsPath(self.embedding_model).is_absolute()
        ):
            raise ValueError(
                "EMBEDDING_MODEL_REVISION must be a full 40-character commit hash so the exact "
                "model files are reproducible"
            )
        if self.embedding_stale_after_seconds <= self.embedding_hard_time_limit_seconds:
            raise ValueError(
                "EMBEDDING_STALE_AFTER_SECONDS must exceed the embedding task hard time limit"
            )
        if self.search_default_top_k > self.search_max_top_k:
            raise ValueError("SEARCH_DEFAULT_TOP_K must not exceed SEARCH_MAX_TOP_K")
        if self.hybrid_dense_weight == 0 and self.hybrid_sparse_weight == 0:
            raise ValueError("HYBRID_DENSE_WEIGHT and HYBRID_SPARSE_WEIGHT cannot both be zero")
        return self

    @property
    def embedding_soft_time_limit_seconds(self) -> int:
        return self.embedding_task_budget_seconds + self.embedding_model_load_allowance_seconds + 30

    @property
    def embedding_hard_time_limit_seconds(self) -> int:
        return self.embedding_soft_time_limit_seconds + 30

    @property
    def processing_soft_time_limit_seconds(self) -> int:
        return self.processing_timeout_seconds + 15

    @property
    def processing_hard_time_limit_seconds(self) -> int:
        return self.processing_timeout_seconds + 45

    @model_validator(mode="after")
    def _enforce_production_safety(self) -> "Settings":
        if self.app_env in ("staging", "production"):
            secret = self.jwt_secret_key.get_secret_value().lower()
            if any(marker in secret for marker in _PLACEHOLDER_MARKERS):
                raise ValueError("JWT_SECRET_KEY must not be a placeholder outside development")
            if self.app_debug:
                raise ValueError("APP_DEBUG must be false in staging and production")
            if self.bcrypt_cost_factor < 12:
                raise ValueError("BCRYPT_COST_FACTOR must be at least 12 in staging and production")
            path = self.storage_local_path
            if not (PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute()):
                raise ValueError(
                    "STORAGE_LOCAL_PATH must be an absolute path in staging and production"
                )
            if not self.rate_limit_enabled:
                raise ValueError("RATE_LIMIT_ENABLED must be true in staging and production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
