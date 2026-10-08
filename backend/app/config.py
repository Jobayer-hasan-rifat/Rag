from functools import lru_cache
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

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
    worker_max_tasks_per_child: int = Field(default=50, ge=1)
    worker_max_memory_per_child_kb: int = Field(default=1_000_000, ge=50_000)

    rate_limit_enabled: bool = True
    rate_limit_auth_attempts: int = Field(default=10, ge=1)
    rate_limit_window_seconds: int = Field(default=60, ge=1)
    rate_limit_upload_attempts: int = Field(default=20, ge=1)

    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

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
