import pytest
from fastapi.testclient import TestClient

from tests.helpers import (
    DEFAULT_PASSWORD,
    bearer,
    db_scalar,
    login,
    register,
    register_and_login,
    tokens,
)

pytestmark = pytest.mark.integration


def _logout(client: TestClient, pair: dict[str, str], *, access: str | None = None) -> int:
    status: int = client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": pair["refresh_token"]},
        headers=bearer(access or pair["access_token"]),
    ).status_code
    return status


def test_me_returns_the_safe_profile_of_the_caller(auth_client: TestClient) -> None:
    pair = register_and_login(auth_client)

    response = auth_client.get("/api/v1/auth/me", headers=bearer(pair["access_token"]))

    assert response.status_code == 200
    data = response.json()["data"]
    assert set(data) == {"id", "email", "display_name", "role", "is_active", "created_at"}
    assert data["email"] == "alice@example.com"
    assert data["role"] == "user"
    assert DEFAULT_PASSWORD not in response.text


def test_me_without_credentials_is_unauthorised(auth_client: TestClient) -> None:
    response = auth_client.get("/api/v1/auth/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_ERROR"
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "header",
    ["Bearer", "Bearer ", "Bearer not-a-jwt", "Basic dXNlcjpwYXNz", "Token abc", "bearer"],
)
def test_me_with_malformed_authorization_header_is_unauthorised(
    auth_client: TestClient, header: str
) -> None:
    response = auth_client.get("/api/v1/auth/me", headers={"Authorization": header})

    assert response.status_code == 401
    assert "Traceback" not in response.text


def test_me_with_tampered_token_is_unauthorised(auth_client: TestClient) -> None:
    pair = register_and_login(auth_client)
    header, payload, signature = pair["access_token"].split(".")
    flipped = signature[:-2] + ("AA" if not signature.endswith("AA") else "BB")

    response = auth_client.get(
        "/api/v1/auth/me", headers=bearer(".".join([header, payload, flipped]))
    )

    assert response.status_code == 401


def test_refresh_token_is_not_accepted_as_an_access_token(auth_client: TestClient) -> None:
    pair = register_and_login(auth_client)

    response = auth_client.get("/api/v1/auth/me", headers=bearer(pair["refresh_token"]))

    assert response.status_code == 401


def test_logout_returns_no_content_and_revokes_the_refresh_token(auth_client: TestClient) -> None:
    pair = register_and_login(auth_client)

    assert _logout(auth_client, pair) == 204

    refresh = auth_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]}
    )
    assert refresh.status_code == 401


def test_logout_invalidates_the_presented_access_token_immediately(auth_client: TestClient) -> None:
    pair = register_and_login(auth_client)
    assert (
        auth_client.get("/api/v1/auth/me", headers=bearer(pair["access_token"])).status_code == 200
    )

    _logout(auth_client, pair)

    assert (
        auth_client.get("/api/v1/auth/me", headers=bearer(pair["access_token"])).status_code == 401
    )


def test_logout_revokes_only_the_current_session(auth_client: TestClient) -> None:
    register(auth_client)
    laptop = tokens(login(auth_client))
    phone = tokens(login(auth_client))

    _logout(auth_client, laptop)

    assert (
        auth_client.get("/api/v1/auth/me", headers=bearer(phone["access_token"])).status_code == 200
    )
    refresh = auth_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": phone["refresh_token"]}
    )
    assert refresh.status_code == 200


def test_logout_requires_authentication(auth_client: TestClient) -> None:
    pair = register_and_login(auth_client)

    response = auth_client.post(
        "/api/v1/auth/logout", json={"refresh_token": pair["refresh_token"]}
    )

    assert response.status_code == 401
    refresh = auth_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]}
    )
    assert refresh.status_code == 200


def test_logout_cannot_revoke_another_users_refresh_token(auth_client: TestClient) -> None:
    alice = register_and_login(auth_client, email="alice@example.com")
    mallory = register_and_login(auth_client, email="mallory@example.com")

    status = auth_client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": alice["refresh_token"]},
        headers=bearer(mallory["access_token"]),
    ).status_code

    assert status == 204  # no oracle about token ownership
    survived = auth_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": alice["refresh_token"]}
    )
    assert survived.status_code == 200


def test_logout_with_unknown_refresh_token_still_revokes_the_access_token(
    auth_client: TestClient,
) -> None:
    pair = register_and_login(auth_client)

    status = auth_client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": "unknown-token-value"},
        headers=bearer(pair["access_token"]),
    ).status_code

    assert status == 204
    assert (
        auth_client.get("/api/v1/auth/me", headers=bearer(pair["access_token"])).status_code == 401
    )


def test_logout_is_idempotent_for_the_refresh_token(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    pair = register_and_login(auth_client)
    second_login = tokens(login(auth_client))

    _logout(auth_client, pair)
    status = auth_client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": pair["refresh_token"]},
        headers=bearer(second_login["access_token"]),
    ).status_code

    assert status == 204
    assert (
        db_scalar(
            migrated_database_url, "SELECT count(*) FROM refresh_tokens WHERE revoked_at IS NULL"
        )
        == 1
    )


def test_logout_requires_a_refresh_token_in_the_body(auth_client: TestClient) -> None:
    pair = register_and_login(auth_client)

    response = auth_client.post(
        "/api/v1/auth/logout", json={}, headers=bearer(pair["access_token"])
    )

    assert response.status_code == 422


def test_access_token_revocation_fails_closed_when_redis_is_unavailable(
    auth_client: TestClient,
) -> None:
    pair = register_and_login(auth_client)
    auth_client.app.state.redis = _BrokenRedis()  # type: ignore[attr-defined]

    response = auth_client.get("/api/v1/auth/me", headers=bearer(pair["access_token"]))

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


class _BrokenRedis:
    def __getattr__(self, name: str) -> object:
        from redis.exceptions import ConnectionError as RedisConnectionError

        async def fail(*args: object, **kwargs: object) -> None:
            raise RedisConnectionError("down")

        return fail

    def pipeline(self, *args: object, **kwargs: object) -> object:
        from redis.exceptions import ConnectionError as RedisConnectionError

        raise RedisConnectionError("down")


class _RevocationWriteFails:
    """Real Redis for reads; writes fail, simulating an outage between auth and revocation."""

    def __init__(self, real: object) -> None:
        self._real = real

    def __getattr__(self, name: str) -> object:
        return getattr(self._real, name)

    async def set(self, *args: object, **kwargs: object) -> None:
        from redis.exceptions import ConnectionError as RedisConnectionError

        raise RedisConnectionError("down")


def test_logout_reports_unavailable_when_revocation_cannot_be_recorded(
    auth_client: TestClient,
) -> None:
    pair = register_and_login(auth_client)
    auth_client.app.state.redis = _RevocationWriteFails(auth_client.app.state.redis)  # type: ignore[attr-defined]

    status = _logout(auth_client, pair)

    assert status == 503
