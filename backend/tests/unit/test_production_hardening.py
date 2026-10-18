"""Settings that are fine on a laptop and must be refused in front of real users."""

from typing import Any

import pytest
from pydantic import ValidationError

from tests.conftest import SettingsFactory

HARDENED: dict[str, Any] = {
    "app_env": "production",
    "bcrypt_cost_factor": 12,
    "cors_origins": ["https://app.example.org"],
    "storage_local_path": "/var/lib/rag/uploads",
}


@pytest.mark.parametrize("env", ["staging", "production"])
@pytest.mark.parametrize(
    "origin",
    [
        "http://app.example.org",
        "https://localhost",
        "https://127.0.0.1:5173",
        "https://app.example.org/path",
        "app.example.org",
    ],
)
def test_cors_origins_must_be_https_origins_outside_development(
    make_settings: SettingsFactory, env: str, origin: str
) -> None:
    with pytest.raises(ValidationError, match="CORS_ORIGINS"):
        make_settings(**{**HARDENED, "app_env": env, "cors_origins": [origin]})


def test_production_may_have_no_cors_origins_when_the_frontend_is_same_origin(
    make_settings: SettingsFactory,
) -> None:
    assert make_settings(**{**HARDENED, "cors_origins": []}).cors_origins == []


def test_development_keeps_its_localhost_origins(make_settings: SettingsFactory) -> None:
    settings = make_settings(app_env="development", cors_origins=["http://localhost:5173"])

    assert settings.cors_origins == ["http://localhost:5173"]


@pytest.mark.parametrize(
    "name",
    ["database_url", "redis_url", "celery_broker_url", "celery_result_backend"],
)
def test_backing_services_must_require_a_password_in_production(
    make_settings: SettingsFactory, name: str
) -> None:
    url = (
        "postgresql+asyncpg://user@db.internal/rag"
        if name == "database_url"
        else "redis://cache.internal:6379/0"
    )
    with pytest.raises(ValidationError, match="must include a password"):
        make_settings(**{**HARDENED, name: url})


def test_a_guessable_signing_key_is_refused_in_production(make_settings: SettingsFactory) -> None:
    with pytest.raises(ValidationError, match="guessable"):
        make_settings(**{**HARDENED, "jwt_secret_key": "a" * 48})


def test_a_remote_model_endpoint_must_use_https_in_production(
    make_settings: SettingsFactory,
) -> None:
    with pytest.raises(ValidationError, match="LLM_BASE_URL must use https"):
        make_settings(
            **{**HARDENED, "rag_enabled": True, "llm_base_url": "http://llm.example.org:8080"}
        )


def test_a_local_model_endpoint_may_use_plain_http_in_production(
    make_settings: SettingsFactory,
) -> None:
    settings = make_settings(
        **{**HARDENED, "rag_enabled": True, "llm_base_url": "http://localhost:11434"}
    )

    assert settings.rag_enabled


def test_interactive_documentation_defaults_to_development_and_test_only(
    make_settings: SettingsFactory,
) -> None:
    assert make_settings(app_env="development").docs_enabled is True
    assert make_settings(app_env="test").docs_enabled is True
    assert make_settings(**HARDENED).docs_enabled is False
    assert make_settings(**{**HARDENED, "api_docs_enabled": True}).docs_enabled is True


def test_transport_security_is_on_by_default_only_outside_development(
    make_settings: SettingsFactory,
) -> None:
    assert make_settings(app_env="development").hsts_active is False
    assert make_settings(**HARDENED).hsts_active is True


@pytest.mark.parametrize("value", ["not-an-ip", "0.0.0.0/0", "::/0", "10.0.0.0/33"])
def test_trusted_proxy_networks_must_be_real_and_not_everything(
    make_settings: SettingsFactory, value: str
) -> None:
    with pytest.raises(ValidationError):
        make_settings(trusted_proxy_cidrs=[value])


def test_trusted_proxy_networks_are_parsed_from_a_comma_separated_value(
    make_settings: SettingsFactory,
) -> None:
    settings = make_settings(trusted_proxy_cidrs="10.0.0.0/8, 192.168.1.5")

    assert [str(n) for n in settings.trusted_proxy_networks] == ["10.0.0.0/8", "192.168.1.5/32"]


def test_empty_environment_values_mean_unset_for_the_optional_hardening_settings(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    # docker compose passes `NAME=` when the variable is not set in .env
    for name in ("API_DOCS_ENABLED", "HSTS_ENABLED", "TRUSTED_PROXY_CIDRS"):
        monkeypatch.setenv(name, "")

    settings = make_settings()

    assert settings.api_docs_enabled is None
    assert settings.hsts_enabled is None
    assert settings.trusted_proxy_cidrs == []
