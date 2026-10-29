"""Phase 14 hardening: response headers, documentation exposure, request caps, who the client is,
what happens when Redis is down, and the budgets on downloads and document management."""

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from redis.asyncio import Redis

from app.main import create_app
from tests.conftest import ClientFactory, SettingsFactory
from tests.files import make_user, text_bytes, upload_ok
from tests.helpers import DEFAULT_PASSWORD, db_execute, db_scalar, login, register, tokens

PRODUCTION: dict[str, Any] = {
    "app_env": "production",
    "bcrypt_cost_factor": 12,
    "cors_origins": ["https://app.example.org"],
    "storage_local_path": "/var/lib/rag/uploads",
}


# --- headers and documentation (no infrastructure needed) -----------------------------------


def test_every_response_carries_the_baseline_security_headers(offline_client: TestClient) -> None:
    for path in ("/health/live", "/api/v1/auth/me", "/no/such/route"):
        headers = offline_client.get(path).headers
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"
        assert headers["referrer-policy"] == "no-referrer"
        assert headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
        assert headers["cache-control"] == "no-store"
        assert "strict-transport-security" not in headers  # not a TLS deployment


def test_the_documentation_pages_keep_their_scripts_and_are_not_given_a_blocking_csp(
    offline_client: TestClient,
) -> None:
    response = offline_client.get("/docs")

    assert response.status_code == 200
    assert "content-security-policy" not in response.headers
    assert response.headers["x-content-type-options"] == "nosniff"


def test_production_serves_no_interactive_documentation_and_sends_hsts(
    make_settings: SettingsFactory,
) -> None:
    with TestClient(create_app(make_settings(**PRODUCTION)), raise_server_exceptions=False) as c:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert c.get(path).status_code == 404
        live = c.get("/health/live")

    assert live.status_code == 200
    assert live.headers["strict-transport-security"] == "max-age=31536000"


def test_documentation_can_be_switched_on_explicitly(make_settings: SettingsFactory) -> None:
    settings = make_settings(**{**PRODUCTION, "api_docs_enabled": True})
    with TestClient(create_app(settings), raise_server_exceptions=False) as c:
        assert c.get("/openapi.json").status_code == 200


@pytest.mark.integration
def test_a_download_keeps_its_own_cache_and_csp_headers(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")
    doc = upload_ok(
        auth_client, alice, filename="a.txt", content=text_bytes(), content_type="text/plain"
    )

    response = auth_client.get(f"/api/v1/documents/{doc['id']}/download", headers=alice)

    assert response.headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert response.headers["cache-control"] == "private, no-store"


# --- request body caps -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/api/v1/auth/login"),
        ("post", "/api/v1/auth/register"),
        ("post", "/api/v1/auth/refresh"),
        ("post", "/api/v1/auth/logout"),
        ("post", "/api/v1/collections"),
        ("patch", "/api/v1/documents/00000000-0000-0000-0000-000000000000"),
    ],
)
def test_json_endpoints_without_their_own_rule_refuse_oversized_bodies_early(
    offline_client: TestClient, method: str, path: str
) -> None:
    body = {"padding": "x" * 70_000}  # over the 64 KiB default, under any parser's patience

    response = getattr(offline_client, method)(path, json=body)

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"
    assert response.json()["error"]["details"]["max_bytes"] == 65_536


def test_a_streamed_body_without_content_length_is_also_capped(offline_client: TestClient) -> None:
    def chunks() -> Iterator[bytes]:
        for _ in range(80):
            yield b"x" * 1024

    response = offline_client.post(
        "/api/v1/auth/login", content=chunks(), headers={"Content-Type": "application/json"}
    )

    # FastAPI turns a failure while reading the body into a 400; what matters is that the body
    # is refused at the cap and never parsed (a 200, 401 or 422 would mean it was read in full)
    assert response.status_code in (400, 413)


@pytest.mark.integration
def test_the_cap_does_not_touch_uploads_or_ordinary_requests(auth_client: TestClient) -> None:
    alice = make_user(auth_client, "alice@example.com")

    big = upload_ok(
        auth_client, alice, filename="big.txt", content=b"a " * 100_000, content_type="text/plain"
    )

    assert big["file_size"] == 200_000  # far over 64 KiB, still accepted as an upload
    assert login(auth_client).status_code == 200


def test_the_default_cap_is_configurable(make_settings: SettingsFactory) -> None:
    settings = make_settings(max_json_body_bytes=2048)
    with TestClient(create_app(settings), raise_server_exceptions=False) as c:
        response = c.post("/api/v1/auth/login", json={"email": "a" * 3000})

    assert response.status_code == 413
    assert response.json()["error"]["details"]["max_bytes"] == 2048


# --- who is the client -----------------------------------------------------------------------


@pytest.fixture
def proxied_app_factory(
    make_settings: SettingsFactory,
    migrated_database_url: str,
    redis_url: str,
    clean_auth_state: None,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Any]:
    clients: list[TestClient] = []

    def factory(peer: str, **overrides: Any) -> TestClient:
        settings = make_settings(
            database_url=migrated_database_url,
            redis_url=f"{redis_url}/0",
            celery_broker_url=f"{redis_url}/1",
            celery_result_backend=f"{redis_url}/2",
            rate_limit_auth_attempts=2,
            **overrides,
        )
        monkeypatch.setenv("CELERY_BROKER_URL", settings.celery_broker_url)
        monkeypatch.setenv("CELERY_RESULT_BACKEND", settings.celery_result_backend)
        client = TestClient(
            create_app(settings), client=(peer, 40000), raise_server_exceptions=False
        )
        client.__enter__()
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.__exit__(None, None, None)


def _refresh_statuses(client: TestClient, forwarded: list[str]) -> list[int]:
    return [
        client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "bogus"},
            headers={"X-Forwarded-For": value},
        ).status_code
        for value in forwarded
    ]


@pytest.mark.integration
def test_behind_a_trusted_proxy_each_real_client_has_its_own_budget(
    proxied_app_factory: Any,
) -> None:
    client = proxied_app_factory("10.0.0.2", trusted_proxy_cidrs=["10.0.0.0/8"])

    first = _refresh_statuses(client, ["198.51.100.1"] * 3)
    other = _refresh_statuses(client, ["198.51.100.2"])

    assert first == [401, 401, 429]
    assert other == [401]  # one noisy client does not throttle the others


@pytest.mark.integration
def test_without_a_trusted_proxy_a_forged_header_cannot_dodge_the_limit(
    proxied_app_factory: Any,
) -> None:
    client = proxied_app_factory("203.0.113.9")

    statuses = _refresh_statuses(client, ["198.51.100.1", "198.51.100.2", "198.51.100.3"])

    assert statuses == [401, 401, 429]


@pytest.mark.integration
def test_a_forged_header_from_outside_the_trusted_network_is_ignored(
    proxied_app_factory: Any,
) -> None:
    client = proxied_app_factory("203.0.113.9", trusted_proxy_cidrs=["10.0.0.0/8"])

    statuses = _refresh_statuses(client, ["198.51.100.1", "198.51.100.2", "198.51.100.3"])

    assert statuses == [401, 401, 429]


# --- Redis down ------------------------------------------------------------------------------


def _break_redis(client: TestClient) -> None:
    client.app.state.redis = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2)  # type: ignore[attr-defined]


@pytest.mark.integration
def test_signing_in_is_refused_not_unlimited_when_the_rate_limiter_is_down(
    auth_client: TestClient,
) -> None:
    register(auth_client)
    _break_redis(auth_client)

    for response in (
        login(auth_client),
        register(auth_client, email="bob@example.com"),
        auth_client.post("/api/v1/auth/refresh", json={"refresh_token": "x" * 20}),
    ):
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
        assert "redis" not in response.text.lower()
        assert "127.0.0.1" not in response.text


# --- download and management budgets ---------------------------------------------------------


@pytest.mark.integration
def test_downloads_have_a_per_user_budget(auth_client_factory: ClientFactory) -> None:
    client = auth_client_factory(rate_limit_download_attempts=2)
    alice = make_user(client, "alice@example.com")
    bob = make_user(client, "bob@example.com")
    doc = upload_ok(
        client, alice, filename="a.txt", content=text_bytes(), content_type="text/plain"
    )
    url = f"/api/v1/documents/{doc['id']}/download"

    statuses = [client.get(url, headers=alice).status_code for _ in range(4)]

    assert statuses == [200, 200, 429, 429]
    assert client.get(f"/api/v1/documents/{doc['id']}/download", headers=bob).status_code == 404


@pytest.mark.integration
def test_renaming_and_deleting_share_a_per_user_budget(auth_client_factory: ClientFactory) -> None:
    client = auth_client_factory(rate_limit_document_manage=3)
    alice = make_user(client, "alice@example.com")
    doc = upload_ok(
        client, alice, filename="a.txt", content=text_bytes(), content_type="text/plain"
    )
    url = f"/api/v1/documents/{doc['id']}"

    renames = [
        client.patch(url, headers=alice, json={"filename": f"n{i}.txt"}).status_code
        for i in range(3)
    ]
    refused_rename = client.patch(url, headers=alice, json={"filename": "z.txt"})
    refused_delete = client.delete(url, headers=alice)

    assert renames == [200, 200, 200]
    assert refused_rename.status_code == 429
    assert refused_delete.status_code == 429
    assert client.get(url, headers=alice).status_code == 200  # reading is not budgeted here


@pytest.mark.integration
def test_unauthenticated_requests_are_refused_before_they_spend_any_budget(
    auth_client_factory: ClientFactory,
) -> None:
    client = auth_client_factory(rate_limit_download_attempts=1)
    alice = make_user(client, "alice@example.com")
    doc = upload_ok(
        client, alice, filename="a.txt", content=text_bytes(), content_type="text/plain"
    )
    url = f"/api/v1/documents/{doc['id']}/download"

    anonymous = [client.get(url).status_code for _ in range(3)]

    assert anonymous == [401, 401, 401]
    assert client.get(url, headers=alice).status_code == 200


# --- expired refresh tokens are removed, live and revoked-but-recent ones are not -------------


@pytest.mark.integration
def test_the_purge_removes_only_long_expired_refresh_tokens(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    from app.workers.maintenance_tasks import _purge

    register(auth_client)
    expired = tokens(login(auth_client))
    live = tokens(login(auth_client))
    revoked = tokens(login(auth_client))
    assert (
        auth_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": revoked["refresh_token"]}
        ).status_code
        == 200
    )  # rotates: the first token of that session is now revoked
    from app.security.refresh_tokens import hash_refresh_token

    db_execute(
        migrated_database_url,
        "UPDATE refresh_tokens SET created_at = now() - interval '10 days', "
        "expires_at = now() - interval '3 days' WHERE token_hash = :h",
        h=hash_refresh_token(expired["refresh_token"]),
    )
    before = db_scalar(migrated_database_url, "SELECT count(*) FROM refresh_tokens")

    deleted = asyncio.run(_purge(auth_client.app.state.settings))  # type: ignore[attr-defined]

    after = db_scalar(migrated_database_url, "SELECT count(*) FROM refresh_tokens")
    assert deleted == 1
    assert after == before - 1
    # the live session still refreshes, and replaying the revoked token is still reuse
    assert (
        auth_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": live["refresh_token"]}
        ).status_code
        == 200
    )
    replay = auth_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": revoked["refresh_token"]}
    )
    assert replay.status_code == 401
    assert login(auth_client, password=DEFAULT_PASSWORD).status_code == 200
