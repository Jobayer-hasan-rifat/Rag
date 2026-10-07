import pytest
from fastapi.testclient import TestClient

from tests.helpers import (
    bearer,
    db_execute,
    db_rows,
    db_scalar,
    login,
    register,
    register_and_login,
    tokens,
)

pytestmark = pytest.mark.integration


def _refresh(client: TestClient, token: str) -> int:
    status: int = client.post("/api/v1/auth/refresh", json={"refresh_token": token}).status_code
    return status


def test_valid_refresh_issues_a_new_token_pair(auth_client: TestClient) -> None:
    original = register_and_login(auth_client)

    response = auth_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": original["refresh_token"]}
    )

    assert response.status_code == 200
    renewed = tokens(response)
    assert renewed["refresh_token"] != original["refresh_token"]
    assert renewed["access_token"] != original["access_token"]
    assert renewed["token_type"] == "bearer"
    assert response.headers["cache-control"] == "no-store"
    assert (
        auth_client.get("/api/v1/auth/me", headers=bearer(renewed["access_token"])).status_code
        == 200
    )


def test_refresh_token_is_single_use_and_rotation_keeps_the_family(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    original = register_and_login(auth_client)

    _refresh(auth_client, original["refresh_token"])

    assert (
        db_scalar(migrated_database_url, "SELECT count(DISTINCT family_id) FROM refresh_tokens")
        == 1
    )
    assert db_scalar(migrated_database_url, "SELECT count(*) FROM refresh_tokens") == 2
    assert (
        db_scalar(
            migrated_database_url, "SELECT count(*) FROM refresh_tokens WHERE revoked_at IS NULL"
        )
        == 1
    )


def test_reusing_a_rotated_token_is_rejected_and_revokes_the_whole_family(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    original = register_and_login(auth_client)
    renewed = tokens(
        auth_client.post("/api/v1/auth/refresh", json={"refresh_token": original["refresh_token"]})
    )

    assert _refresh(auth_client, original["refresh_token"]) == 401  # replay by a thief
    assert _refresh(auth_client, renewed["refresh_token"]) == 401  # legitimate token also dead

    assert (
        db_scalar(
            migrated_database_url, "SELECT count(*) FROM refresh_tokens WHERE revoked_at IS NULL"
        )
        == 0
    )


def test_reuse_detection_does_not_affect_other_sessions(auth_client: TestClient) -> None:
    register(auth_client)
    first = tokens(login(auth_client))
    second = tokens(login(auth_client))
    tokens(auth_client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}))

    assert _refresh(auth_client, first["refresh_token"]) == 401

    assert _refresh(auth_client, second["refresh_token"]) == 200


def test_expired_refresh_token_is_rejected(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    original = register_and_login(auth_client)
    db_execute(
        migrated_database_url,
        "UPDATE refresh_tokens SET created_at = now() - interval '8 days', "
        "expires_at = now() - interval '1 day'",
    )

    response = auth_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": original["refresh_token"]}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_ERROR"


@pytest.mark.parametrize("token", ["garbage", "a" * 64, "x" * 511, "eyJhbGciOiJIUzI1NiJ9.e30.sig"])
def test_unknown_refresh_token_is_rejected(auth_client: TestClient, token: str) -> None:
    assert _refresh(auth_client, token) == 401


def test_a_jwt_access_token_cannot_be_used_as_a_refresh_token(auth_client: TestClient) -> None:
    pair = register_and_login(auth_client)

    assert _refresh(auth_client, pair["access_token"]) == 401


@pytest.mark.parametrize("payload", [{}, {"refresh_token": ""}, {"refresh_token": "x" * 513}])
def test_malformed_refresh_requests_are_rejected(
    auth_client: TestClient, payload: dict[str, str]
) -> None:
    assert auth_client.post("/api/v1/auth/refresh", json=payload).status_code == 422


def test_refresh_fails_and_revokes_family_for_a_deactivated_user(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    original = register_and_login(auth_client)
    db_execute(migrated_database_url, "UPDATE users SET is_active = false")

    assert _refresh(auth_client, original["refresh_token"]) == 401
    assert (
        db_scalar(
            migrated_database_url, "SELECT count(*) FROM refresh_tokens WHERE revoked_at IS NULL"
        )
        == 0
    )


def test_refresh_token_never_appears_in_database_in_clear(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    original = register_and_login(auth_client)
    renewed = tokens(
        auth_client.post("/api/v1/auth/refresh", json={"refresh_token": original["refresh_token"]})
    )

    hashes = [
        row[0] for row in db_rows(migrated_database_url, "SELECT token_hash FROM refresh_tokens")
    ]

    assert original["refresh_token"] not in hashes
    assert renewed["refresh_token"] not in hashes
