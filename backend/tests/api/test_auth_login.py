import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

from app.security.jwt import JWTService
from tests.helpers import DEFAULT_PASSWORD, bearer, create_user_in_db, db_scalar, login, register

pytestmark = pytest.mark.integration


def test_valid_login_returns_a_token_pair(auth_client: TestClient) -> None:
    register(auth_client)

    response = login(auth_client)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["token_type"] == "bearer"
    assert data["expires_in"] == 900
    assert data["access_token"].count(".") == 2
    assert len(data["refresh_token"]) >= 64


def test_token_responses_must_not_be_cached(auth_client: TestClient) -> None:
    register(auth_client)

    response = login(auth_client)

    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"


def test_access_token_identifies_the_user_and_expires_in_fifteen_minutes(
    auth_client: TestClient,
) -> None:
    user_id = register(auth_client).json()["data"]["id"]

    access = login(auth_client).json()["data"]["access_token"]

    claims = jwt.decode(access, options={"verify_signature": False})
    assert claims["sub"] == user_id
    assert claims["type"] == "access"
    assert claims["exp"] - claims["iat"] == 900
    assert "password" not in str(claims).lower()


def test_login_is_case_insensitive_for_email(auth_client: TestClient) -> None:
    register(auth_client)

    assert login(auth_client, email="ALICE@Example.com").status_code == 200


def test_refresh_token_is_stored_only_as_a_hash(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    register(auth_client)
    refresh = login(auth_client).json()["data"]["refresh_token"]

    stored = db_scalar(migrated_database_url, "SELECT token_hash FROM refresh_tokens")

    assert stored != refresh
    assert refresh not in stored
    assert len(stored) == 64


def test_each_login_starts_a_separate_token_family(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    register(auth_client)
    login(auth_client)
    login(auth_client)

    assert (
        db_scalar(migrated_database_url, "SELECT count(DISTINCT family_id) FROM refresh_tokens")
        == 2
    )


def test_wrong_password_is_rejected(auth_client: TestClient) -> None:
    register(auth_client)

    response = login(auth_client, password="Wr0ngPassword")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_ERROR"
    assert response.headers["www-authenticate"] == "Bearer"


def test_unknown_account_is_indistinguishable_from_wrong_password(auth_client: TestClient) -> None:
    register(auth_client)

    wrong_password = login(auth_client, password="Wr0ngPassword")
    unknown_account = login(auth_client, email="nobody@example.com")

    assert unknown_account.status_code == wrong_password.status_code == 401
    assert unknown_account.json()["error"] == wrong_password.json()["error"]


def test_inactive_account_cannot_log_in_and_looks_like_bad_credentials(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    create_user_in_db(migrated_database_url, email="inactive@example.com", is_active=False)

    response = login(auth_client, email="inactive@example.com", password=DEFAULT_PASSWORD)
    reference = login(auth_client, email="inactive@example.com", password="Wr0ngPassword")

    assert response.status_code == 401
    assert response.json()["error"] == reference.json()["error"]


def test_soft_deleted_account_cannot_log_in(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    create_user_in_db(migrated_database_url, email="gone@example.com", deleted=True)

    assert login(auth_client, email="gone@example.com").status_code == 401


@pytest.mark.parametrize(
    "payload",
    [{}, {"email": "a@example.com"}, {"password": "x"}, {"email": "bad", "password": "x"}],
)
def test_malformed_login_requests_are_rejected(
    auth_client: TestClient, payload: dict[str, str]
) -> None:
    assert auth_client.post("/api/v1/auth/login", json=payload).status_code == 422


def test_login_ignores_unknown_fields_by_rejecting_them(auth_client: TestClient) -> None:
    register(auth_client)

    response = auth_client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": DEFAULT_PASSWORD, "role": "admin"},
    )

    assert response.status_code == 422


def test_oversized_password_is_rejected_cheaply(auth_client: TestClient) -> None:
    response = login(auth_client, password="x" * 5000)

    assert response.status_code == 422


def test_password_over_bcrypt_limit_cannot_authenticate(auth_client: TestClient) -> None:
    register(auth_client)

    assert login(auth_client, password=DEFAULT_PASSWORD + "x" * 100).status_code == 401


def test_sql_injection_in_login_is_inert(auth_client: TestClient) -> None:
    register(auth_client)

    response = login(auth_client, email="alice@example.com' OR '1'='1", password="x")

    assert response.status_code == 422


def test_user_deactivated_after_login_loses_access_immediately(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    from tests.helpers import db_execute

    register(auth_client)
    access = login(auth_client).json()["data"]["access_token"]
    assert auth_client.get("/api/v1/auth/me", headers=bearer(access)).status_code == 200

    db_execute(migrated_database_url, "UPDATE users SET is_active = false")

    assert auth_client.get("/api/v1/auth/me", headers=bearer(access)).status_code == 401


def test_token_for_deleted_user_is_rejected(auth_client: TestClient) -> None:
    jwt_service: JWTService = auth_client.app.state.jwt_service  # type: ignore[attr-defined]
    token = jwt_service.create_access_token(uuid.uuid4(), now=datetime.now(UTC)).token

    assert auth_client.get("/api/v1/auth/me", headers=bearer(token)).status_code == 401


def test_expired_access_token_is_rejected(auth_client: TestClient) -> None:
    user_id = uuid.UUID(register(auth_client).json()["data"]["id"])
    jwt_service: JWTService = auth_client.app.state.jwt_service  # type: ignore[attr-defined]
    expired = jwt_service.create_access_token(user_id, now=datetime.now(UTC) - timedelta(hours=1))

    response = auth_client.get("/api/v1/auth/me", headers=bearer(expired.token))

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Invalid or expired token"
