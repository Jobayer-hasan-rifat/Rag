from functools import lru_cache
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

    rate_limit_enabled: bool = True
    rate_limit_auth_attempts: int = Field(default=10, ge=1)
    rate_limit_window_seconds: int = Field(default=60, ge=1)

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
    def _enforce_production_safety(self) -> "Settings":
        if self.app_env in ("staging", "production"):
            secret = self.jwt_secret_key.get_secret_value().lower()
            if any(marker in secret for marker in _PLACEHOLDER_MARKERS):
                raise ValueError("JWT_SECRET_KEY must not be a placeholder outside development")
            if self.app_debug:
                raise ValueError("APP_DEBUG must be false in staging and production")
            if self.bcrypt_cost_factor < 12:
                raise ValueError("BCRYPT_COST_FACTOR must be at least 12 in staging and production")
            if not self.rate_limit_enabled:
                raise ValueError("RATE_LIMIT_ENABLED must be true in staging and production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
