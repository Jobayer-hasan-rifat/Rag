import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import (
    UNREACHABLE_DATABASE_URL,
    UNREACHABLE_REDIS_URL,
    SettingsFactory,
)

HEALTH_PREFIXES = ["/health", "/api/v1/health"]


@pytest.mark.parametrize("prefix", HEALTH_PREFIXES)
def test_liveness_returns_alive_without_dependencies(
    offline_client: TestClient, prefix: str
) -> None:
    response = offline_client.get(f"{prefix}/live")

    assert response.status_code == 200
    body = response.json()
    assert body["data"] == {"status": "alive"}
    assert body["meta"]["request_id"] == response.headers["x-request-id"]
    assert body["meta"]["timestamp"]


@pytest.mark.parametrize("prefix", HEALTH_PREFIXES)
def test_health_summary_reports_service_identity(offline_client: TestClient, prefix: str) -> None:
    response = offline_client.get(prefix)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "alive"
    assert data["environment"] == "test"
    assert data["version"]


@pytest.mark.parametrize("prefix", HEALTH_PREFIXES)
def test_readiness_is_503_when_dependencies_are_down(
    offline_client: TestClient, prefix: str
) -> None:
    response = offline_client.get(f"{prefix}/ready")

    assert response.status_code == 503
    data = response.json()["data"]
    assert data["status"] == "unavailable"
    assert data["checks"] == {"database": "unavailable", "redis": "unavailable"}


def test_readiness_failure_does_not_leak_connection_details(offline_client: TestClient) -> None:
    response = offline_client.get("/health/ready")

    body = response.text
    assert "secret-db-password" not in body
    assert "secret-redis-password" not in body
    assert UNREACHABLE_DATABASE_URL not in body
    assert UNREACHABLE_REDIS_URL not in body
    assert "127.0.0.1" not in body


@pytest.mark.integration
@pytest.mark.parametrize("prefix", HEALTH_PREFIXES)
def test_readiness_is_200_when_dependencies_are_up(infra_client: TestClient, prefix: str) -> None:
    response = infra_client.get(f"{prefix}/ready")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data == {"status": "ready", "checks": {"database": "ok", "redis": "ok"}}


@pytest.mark.integration
def test_readiness_reports_only_the_failing_dependency(
    make_settings: SettingsFactory, postgres_url: str
) -> None:
    settings = make_settings(database_url=postgres_url)
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["data"]["checks"] == {"database": "ok", "redis": "unavailable"}
