import pytest
from fastapi.testclient import TestClient

from tests.helpers import DEFAULT_PASSWORD, db_rows, db_scalar, register

pytestmark = pytest.mark.integration


def test_valid_registration_creates_a_standard_user(auth_client: TestClient) -> None:
    response = register(auth_client)

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["email"] == "alice@example.com"
    assert data["display_name"] == "Alice"
    assert data["role"] == "user"
    assert data["is_active"] is True
    assert data["id"]
    assert response.json()["meta"]["request_id"] == response.headers["x-request-id"]


def test_response_never_contains_password_or_hash(auth_client: TestClient) -> None:
    response = register(auth_client)

    body = response.text
    assert DEFAULT_PASSWORD not in body
    assert "password" not in body.lower()
    assert "$2b$" not in body


def test_password_is_stored_as_a_bcrypt_hash(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    register(auth_client)

    stored = db_scalar(
        migrated_database_url, "SELECT password_hash FROM users WHERE email = 'alice@example.com'"
    )

    assert stored.startswith("$2b$04$")
    assert DEFAULT_PASSWORD not in stored


def test_email_is_normalised_before_storage(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    response = register(auth_client, email="  Alice@Example.COM ")

    assert response.status_code == 201
    assert response.json()["data"]["email"] == "alice@example.com"
    assert db_scalar(migrated_database_url, "SELECT email FROM users") == "alice@example.com"


@pytest.mark.parametrize("email", ["not-an-email", "a@", "", "x" * 300 + "@example.com"])
def test_invalid_email_is_rejected(auth_client: TestClient, email: str) -> None:
    response = register(auth_client, email=email)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("password", ["short1A", "alllowercase1", "ALLUPPERCASE1", "NoDigitsHere"])
def test_weak_password_is_rejected_without_echoing_it(
    auth_client: TestClient, password: str
) -> None:
    response = register(auth_client, password=password)

    assert response.status_code == 422
    assert password not in response.text


def test_duplicate_email_is_rejected_with_neutral_message(auth_client: TestClient) -> None:
    register(auth_client)

    response = register(auth_client, display_name="Someone Else")

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "CONFLICT"
    assert "alice" not in response.text.lower()
    assert "already" not in error["message"].lower()


def test_duplicate_email_differing_only_by_case_is_rejected(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    register(auth_client)

    response = register(auth_client, email="ALICE@EXAMPLE.COM")

    assert response.status_code == 409
    assert db_scalar(migrated_database_url, "SELECT count(*) FROM users") == 1


@pytest.mark.parametrize("field", ["role", "is_active", "is_superuser", "id", "password_hash"])
def test_privilege_escalation_fields_are_rejected(auth_client: TestClient, field: str) -> None:
    response = auth_client.post(
        "/api/v1/auth/register",
        json={
            "email": "mallory@example.com",
            "password": DEFAULT_PASSWORD,
            "display_name": "Mallory",
            field: "admin",
        },
    )

    assert response.status_code == 422


def test_registered_user_always_gets_the_user_role(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    register(auth_client)

    role = db_scalar(
        migrated_database_url,
        "SELECT r.name FROM users u JOIN roles r ON r.id = u.role_id",
    )

    assert role == "user"


@pytest.mark.parametrize("payload", [{}, {"email": "a@example.com"}, {"password": "x"}])
def test_missing_fields_are_rejected(auth_client: TestClient, payload: dict[str, str]) -> None:
    assert auth_client.post("/api/v1/auth/register", json=payload).status_code == 422


def test_non_json_body_is_rejected(auth_client: TestClient) -> None:
    response = auth_client.post(
        "/api/v1/auth/register", content="email=a", headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 422


def test_failed_registration_creates_no_user(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    register(auth_client, password="weak")

    assert db_rows(migrated_database_url, "SELECT 1 FROM users") == []


def test_missing_default_role_is_a_server_error_without_details(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    from tests.helpers import db_execute

    db_execute(migrated_database_url, "DELETE FROM roles WHERE name = 'user'")
    try:
        response = register(auth_client)
    finally:
        db_execute(
            migrated_database_url,
            "INSERT INTO roles (name, description) VALUES ('user', 'Standard user')",
        )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "role" not in response.text.lower()
