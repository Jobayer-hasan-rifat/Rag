import pytest
from fastapi.testclient import TestClient

from tests.conftest import ClientFactory
from tests.helpers import DEFAULT_PASSWORD, login, register, register_and_login

pytestmark = pytest.mark.integration


@pytest.fixture
def limited(auth_client_factory: ClientFactory) -> TestClient:
    return auth_client_factory(rate_limit_auth_attempts=3, rate_limit_window_seconds=60)


def test_login_attempts_beyond_the_budget_are_throttled(limited: TestClient) -> None:
    statuses = [login(limited, password="Wr0ngPassword1").status_code for _ in range(5)]

    assert statuses == [401, 401, 401, 429, 429]


def test_throttled_response_uses_the_error_envelope_and_retry_after(limited: TestClient) -> None:
    for _ in range(3):
        login(limited)

    response = login(limited)

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"
    assert 1 <= int(response.headers["retry-after"]) <= 60
    assert response.json()["meta"]["request_id"]


def test_throttling_applies_before_credentials_are_checked(limited: TestClient) -> None:
    register(limited)
    for _ in range(3):
        login(limited, password="Wr0ngPassword1")

    assert login(limited, password=DEFAULT_PASSWORD).status_code == 429


def test_registration_is_throttled(limited: TestClient) -> None:
    statuses = [register(limited, email=f"user{i}@example.com").status_code for i in range(5)]

    assert statuses[:3] == [201, 201, 201]
    assert statuses[3:] == [429, 429]


def test_refresh_is_throttled(limited: TestClient) -> None:
    statuses = [
        limited.post("/api/v1/auth/refresh", json={"refresh_token": "bogus"}).status_code
        for _ in range(5)
    ]

    assert statuses == [401, 401, 401, 429, 429]


def test_budgets_are_separate_per_endpoint(limited: TestClient) -> None:
    for _ in range(3):
        login(limited)

    assert register(limited).status_code == 201


def test_one_client_cannot_exhaust_the_per_account_budget(
    auth_client_factory: ClientFactory,
) -> None:
    client = auth_client_factory(rate_limit_auth_attempts=2)
    statuses = [
        login(client, email="victim@example.com", password="Wr0ngPassword1").status_code
        for _ in range(6)
    ]

    from redis import Redis

    redis_client = Redis.from_url(str(client.app.state.settings.redis_url))  # type: ignore[attr-defined]
    try:
        keys = [k.decode() for k in redis_client.scan_iter("ratelimit:login:email:*")]
        counted = int(redis_client.get(keys[0]) or 0)  # type: ignore[arg-type]
    finally:
        redis_client.close()

    assert statuses == [401, 401, 429, 429, 429, 429]
    assert len(keys) == 1
    assert "victim" not in keys[0]
    assert counted == 2  # blocked attempts were stopped by the per-IP check first


def test_rate_limit_can_be_disabled_for_development(auth_client_factory: ClientFactory) -> None:
    client = auth_client_factory(rate_limit_enabled=False, rate_limit_auth_attempts=1)

    statuses = [login(client).status_code for _ in range(4)]

    assert 429 not in statuses


def test_normal_usage_is_not_throttled(auth_client: TestClient) -> None:
    pair = register_and_login(auth_client)

    assert pair["access_token"]
