import pytest
from pydantic import ValidationError

from app.config import Settings
from tests.conftest import SettingsFactory

REQUIRED_VARS = (
    "DATABASE_URL",
    "REDIS_URL",
    "CELERY_BROKER_URL",
    "CELERY_RESULT_BACKEND",
    "JWT_SECRET_KEY",
)


def test_settings_load_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@db:5432/app")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://redis:6379/1")
    monkeypatch.setenv("CELERY_RESULT_BACKEND", "redis://redis:6379/2")
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 40)
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173, http://localhost:3000")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    settings = Settings(_env_file=None)

    assert settings.database_url.endswith("/app")
    assert settings.cors_origins == ["http://localhost:5173", "http://localhost:3000"]
    assert settings.log_level == "DEBUG"
    assert settings.app_debug is False


@pytest.mark.parametrize("missing", REQUIRED_VARS)
def test_missing_required_variable_fails_fast(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    values = {
        "DATABASE_URL": "postgresql+asyncpg://u:p@db:5432/app",
        "REDIS_URL": "redis://redis:6379/0",
        "CELERY_BROKER_URL": "redis://redis:6379/1",
        "CELERY_RESULT_BACKEND": "redis://redis:6379/2",
        "JWT_SECRET_KEY": "x" * 40,
    }
    for name in REQUIRED_VARS:
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        if name != missing:
            monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None)

    assert missing.lower() in str(error.value)


def test_wildcard_cors_origin_is_rejected(make_settings: SettingsFactory) -> None:
    with pytest.raises(ValidationError, match="not allowed"):
        make_settings(cors_origins="*")


def test_database_url_must_use_asyncpg(make_settings: SettingsFactory) -> None:
    with pytest.raises(ValidationError, match="asyncpg"):
        make_settings(database_url="postgresql://u:p@db/app")


def test_redis_urls_must_use_redis_scheme(make_settings: SettingsFactory) -> None:
    with pytest.raises(ValidationError, match="redis"):
        make_settings(celery_broker_url="amqp://guest@broker//")


def test_short_jwt_secret_is_rejected(make_settings: SettingsFactory) -> None:
    with pytest.raises(ValidationError, match="32 characters"):
        make_settings(jwt_secret_key="short")


@pytest.mark.parametrize("env", ["staging", "production"])
def test_placeholder_secret_is_rejected_outside_development(
    make_settings: SettingsFactory, env: str
) -> None:
    with pytest.raises(ValidationError, match="placeholder"):
        make_settings(app_env=env, jwt_secret_key="change_this_" + "x" * 30)


def test_placeholder_secret_is_allowed_in_development(make_settings: SettingsFactory) -> None:
    settings = make_settings(app_env="development", jwt_secret_key="change_this_" + "x" * 30)
    assert settings.app_env == "development"


def test_debug_mode_is_rejected_in_production(make_settings: SettingsFactory) -> None:
    with pytest.raises(ValidationError, match="APP_DEBUG"):
        make_settings(app_env="production", app_debug=True)


def test_invalid_log_level_is_rejected(make_settings: SettingsFactory) -> None:
    with pytest.raises(ValidationError):
        make_settings(log_level="CHATTY")


def test_secret_is_not_exposed_in_repr(make_settings: SettingsFactory) -> None:
    settings = make_settings()
    assert "test-only-secret" not in repr(settings)
