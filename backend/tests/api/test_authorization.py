import uuid

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.dependencies import AdminUser, CurrentUser, OptionalUser, require_roles
from app.security.authorization import RoleName, require_owner_or_admin
from tests.conftest import ClientFactory
from tests.helpers import (
    DEFAULT_PASSWORD,
    bearer,
    create_user_in_db,
    login,
    register_and_login,
    tokens,
)

pytestmark = pytest.mark.integration

OWNED: dict[str, uuid.UUID] = {}


def _configure(app: FastAPI) -> None:
    @app.get("/_test/admin-only")
    async def admin_only(user: AdminUser) -> dict[str, str]:
        return {"role": user.role_name}

    @app.get("/_test/any-user")
    async def any_user(user: CurrentUser) -> dict[str, str]:
        return {"email": user.email}

    @app.get("/_test/optional")
    async def optional(user: OptionalUser) -> dict[str, str | None]:
        return {"email": user.email if user else None}

    @app.get("/_test/user-role-only", dependencies=[Depends(require_roles(RoleName.USER))])
    async def user_only() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/_test/resources/{resource_id}")
    async def resource(resource_id: str, user: CurrentUser) -> dict[str, str]:
        require_owner_or_admin(
            actor_id=user.id, actor_role=user.role_name, owner_id=OWNED[resource_id]
        )
        return {"resource": resource_id}


@pytest.fixture
def client(auth_client_factory: ClientFactory) -> TestClient:
    return auth_client_factory(_configure)


@pytest.fixture
def admin_token(client: TestClient, migrated_database_url: str) -> str:
    create_user_in_db(migrated_database_url, email="admin@example.com", role="admin")
    return str(
        tokens(login(client, email="admin@example.com", password=DEFAULT_PASSWORD))["access_token"]
    )


def test_protected_route_requires_authentication(client: TestClient) -> None:
    response = client.get("/_test/any-user")

    assert response.status_code == 401


def test_authenticated_user_reaches_protected_route(client: TestClient) -> None:
    pair = register_and_login(client)

    response = client.get("/_test/any-user", headers=bearer(pair["access_token"]))

    assert response.json() == {"email": "alice@example.com"}


def test_normal_user_is_forbidden_from_admin_routes(client: TestClient) -> None:
    pair = register_and_login(client)

    response = client.get("/_test/admin-only", headers=bearer(pair["access_token"]))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "AUTHORIZATION_ERROR"


def test_unauthenticated_request_to_admin_route_is_401_not_403(client: TestClient) -> None:
    assert client.get("/_test/admin-only").status_code == 401


def test_admin_reaches_admin_routes(client: TestClient, admin_token: str) -> None:
    response = client.get("/_test/admin-only", headers=bearer(admin_token))

    assert response.status_code == 200
    assert response.json() == {"role": "admin"}


def test_role_requirement_is_exact(client: TestClient, admin_token: str) -> None:
    assert client.get("/_test/user-role-only", headers=bearer(admin_token)).status_code == 403


def test_optional_authentication_allows_anonymous_callers(client: TestClient) -> None:
    assert client.get("/_test/optional").json() == {"email": None}


def test_optional_authentication_identifies_valid_tokens(client: TestClient) -> None:
    pair = register_and_login(client)

    response = client.get("/_test/optional", headers=bearer(pair["access_token"]))

    assert response.json() == {"email": "alice@example.com"}


def test_optional_authentication_still_rejects_invalid_tokens(client: TestClient) -> None:
    assert client.get("/_test/optional", headers=bearer("garbage")).status_code == 401


def test_user_cannot_access_another_users_resource(client: TestClient) -> None:
    alice = register_and_login(client, email="alice@example.com")
    bob = register_and_login(client, email="bob@example.com")
    alice_id = client.get("/api/v1/auth/me", headers=bearer(alice["access_token"])).json()["data"][
        "id"
    ]
    OWNED["alice-doc"] = uuid.UUID(alice_id)

    own = client.get("/_test/resources/alice-doc", headers=bearer(alice["access_token"]))
    other = client.get("/_test/resources/alice-doc", headers=bearer(bob["access_token"]))

    assert own.status_code == 200
    assert other.status_code == 403


def test_admin_can_access_any_users_resource(client: TestClient, admin_token: str) -> None:
    alice = register_and_login(client, email="alice@example.com")
    alice_id = client.get("/api/v1/auth/me", headers=bearer(alice["access_token"])).json()["data"][
        "id"
    ]
    OWNED["alice-doc-2"] = uuid.UUID(alice_id)

    response = client.get("/_test/resources/alice-doc-2", headers=bearer(admin_token))

    assert response.status_code == 200


def test_role_is_read_from_the_database_not_from_the_token(
    client: TestClient, migrated_database_url: str
) -> None:
    from tests.helpers import db_execute

    pair = register_and_login(client)
    assert client.get("/_test/admin-only", headers=bearer(pair["access_token"])).status_code == 403

    db_execute(
        migrated_database_url,
        "UPDATE users SET role_id = (SELECT id FROM roles WHERE name = 'admin')",
    )

    assert client.get("/_test/admin-only", headers=bearer(pair["access_token"])).status_code == 200


def test_clients_cannot_escalate_by_forging_a_role_claim(client: TestClient) -> None:
    import jwt

    pair = register_and_login(client)
    claims = jwt.decode(pair["access_token"], options={"verify_signature": False})
    forged = jwt.encode({**claims, "role": "admin"}, "attacker-guess-0123456789abcdef-xyz", "HS256")

    assert client.get("/_test/admin-only", headers=bearer(forged)).status_code == 401


def test_demoted_admin_loses_access_immediately(
    client: TestClient, admin_token: str, migrated_database_url: str
) -> None:
    from tests.helpers import db_execute

    assert client.get("/_test/admin-only", headers=bearer(admin_token)).status_code == 200

    db_execute(
        migrated_database_url,
        "UPDATE users SET role_id = (SELECT id FROM roles WHERE name = 'user')",
    )

    assert client.get("/_test/admin-only", headers=bearer(admin_token)).status_code == 403
