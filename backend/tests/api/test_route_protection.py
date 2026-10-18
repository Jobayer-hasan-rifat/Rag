"""Every route is protected unless it is deliberately public, and identifiers cannot be mixed."""

import uuid

import pytest
from fastapi.testclient import TestClient

from tests.conftest import ClientFactory
from tests.files import make_user, text_bytes, upload_ok
from tests.search_helpers import ready_doc
from tests.version_helpers import DOCS, ready_version

# The only routes an anonymous caller may use: probes, and the three that obtain a session.
PUBLIC = {
    ("GET", "/health"),
    ("GET", "/health/live"),
    ("GET", "/health/ready"),
    ("GET", "/api/v1/health"),
    ("GET", "/api/v1/health/live"),
    ("GET", "/api/v1/health/ready"),
    ("POST", "/api/v1/auth/register"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/refresh"),
}


def _operations(client: TestClient) -> list[tuple[str, str]]:
    """Every documented operation, read from the OpenAPI schema so it covers whatever routers the
    framework wraps (the route list itself is not a stable thing to walk)."""
    schema = client.app.openapi()  # type: ignore[attr-defined]
    return [
        (method.upper(), path)
        for path, item in schema["paths"].items()
        for method in item
        if method in {"get", "post", "put", "patch", "delete"}
    ]


def _fill(path: str) -> str:
    out = path
    while "{" in out:
        start = out.index("{")
        end = out.index("}")
        out = out[:start] + str(uuid.uuid4()) + out[end + 1 :]
    return out


def test_the_application_has_routes_to_check(offline_client: TestClient) -> None:
    assert len(_operations(offline_client)) >= 25


def test_every_non_public_route_refuses_an_anonymous_caller(offline_client: TestClient) -> None:
    unprotected = []
    for method, path in _operations(offline_client):
        if (method, path) in PUBLIC:
            continue
        response = offline_client.request(method, _fill(path))
        if response.status_code != 401:
            unprotected.append(f"{method} {path} -> {response.status_code}")

    assert unprotected == []


@pytest.mark.parametrize(
    "authorization",
    ["Bearer not-a-token", "Bearer ", "Basic YWxpY2U6cGFzc3dvcmQ=", "bearer"],
)
def test_every_non_public_route_refuses_a_bad_credential(
    offline_client: TestClient, authorization: str
) -> None:
    unprotected = []
    for method, path in _operations(offline_client):
        if (method, path) in PUBLIC:
            continue
        response = offline_client.request(
            method, _fill(path), headers={"Authorization": authorization}
        )
        if response.status_code != 401:
            unprotected.append(f"{method} {path} -> {response.status_code}")

    assert unprotected == []


@pytest.mark.integration
def test_a_version_of_one_document_cannot_be_used_through_another_documents_path(
    auth_client_factory: ClientFactory,
) -> None:
    client = auth_client_factory()
    alice = make_user(client, "alice@example.com")
    first = ready_doc(client, alice, "Rice seedlings are transplanted in July.", "a.txt")
    second = ready_doc(client, alice, "Fish are caught in the monsoon season.", "b.txt")
    second_v2 = ready_version(client, alice, second, "Fish are caught in the winter season.")
    before = client.get(f"{DOCS}/{second}/versions", headers=alice).json()["data"]

    attempts = [
        client.get(f"{DOCS}/{first}/versions/{second_v2}", headers=alice),
        client.post(f"{DOCS}/{first}/versions/{second_v2}/activate", headers=alice),
        client.post(f"{DOCS}/{first}/versions/{second_v2}/retry", headers=alice),
        client.delete(f"{DOCS}/{first}/versions/{second_v2}", headers=alice),
    ]

    assert [r.status_code for r in attempts] == [404, 404, 404, 404]
    after = client.get(f"{DOCS}/{second}/versions", headers=alice).json()["data"]
    assert after == before  # nothing was activated, queued or deleted


@pytest.mark.integration
def test_a_foreign_version_id_in_a_search_filter_is_not_found_and_leaks_nothing(
    auth_client_factory: ClientFactory,
) -> None:
    client = auth_client_factory()
    alice = make_user(client, "alice@example.com")
    bob = make_user(client, "bob@example.com")
    secret = upload_ok(
        client, alice, filename="s.txt", content=text_bytes("secret"), content_type="text/plain"
    )

    response = client.post(
        "/api/v1/search/hybrid",
        headers=bob,
        json={"query": "secret", "document_ids": [secret["id"], secret["version_id"]]},
    )

    assert response.status_code == 404
    assert "secret" not in response.text
