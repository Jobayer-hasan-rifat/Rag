import json
import logging
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.observability.logging import JsonFormatter, RequestContextFilter
from tests.conftest import ClientFactory
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


class LogCapture:
    def __init__(self) -> None:
        self.lines: list[str] = []
        capture = self

        class Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                capture.lines.append(self.format(record))

        self._handler = Capture(level=logging.DEBUG)
        self._handler.setFormatter(JsonFormatter())
        self._handler.addFilter(RequestContextFilter("test"))

    def attach(self) -> None:
        # create_app() resets root handlers, so attach after the app has been created.
        logging.getLogger().addHandler(self._handler)

    def detach(self) -> None:
        logging.getLogger().removeHandler(self._handler)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@pytest.fixture
def capture() -> Iterator[LogCapture]:
    log_capture = LogCapture()
    yield log_capture
    log_capture.detach()


def _client(factory: ClientFactory, capture: LogCapture) -> TestClient:
    client = factory(log_level="DEBUG")
    capture.attach()
    return client


def test_full_auth_flow_never_logs_secrets(
    auth_client_factory: ClientFactory, capture: LogCapture
) -> None:
    client = _client(auth_client_factory, capture)
    secret_password = "Sup3rSecretPassw0rd"

    register(client, password=secret_password)
    pair = tokens(login(client, password=secret_password))
    login(client, password="Wr0ngPassword1")
    client.get("/api/v1/auth/me", headers=bearer(pair["access_token"]))
    renewed = tokens(
        client.post("/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    )
    client.post("/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]})  # reuse
    client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": renewed["refresh_token"]},
        headers=bearer(renewed["access_token"]),
    )

    assert capture.lines, "expected log output to be captured"
    for secret in (
        secret_password,
        "Wr0ngPassword1",
        pair["access_token"],
        pair["refresh_token"],
        renewed["access_token"],
        renewed["refresh_token"],
        "$2b$",
        "Bearer ",
        "test-only-secret-key",
    ):
        assert secret not in capture.text


def test_failed_logins_do_not_log_the_email_address(
    auth_client_factory: ClientFactory, capture: LogCapture
) -> None:
    client = _client(auth_client_factory, capture)

    login(client, email="victim@example.com", password="Wr0ngPassword1")
    register(client, email="victim2@example.com")
    register(client, email="victim2@example.com")

    assert "victim" not in capture.text


def test_security_events_are_logged_with_a_reason(
    auth_client_factory: ClientFactory, capture: LogCapture
) -> None:
    client = _client(auth_client_factory, capture)
    register(client)
    login(client, password="Wr0ngPassword1")

    events = [json.loads(line) for line in capture.lines]

    failed = [e for e in events if e["message"] == "login failed"]
    assert failed and failed[0]["reason"] == "invalid_credentials"
    assert failed[0]["request_id"]


def test_reuse_of_a_refresh_token_is_logged_as_a_warning(
    auth_client_factory: ClientFactory, capture: LogCapture
) -> None:
    client = _client(auth_client_factory, capture)
    pair = register_and_login(client)
    client.post("/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    client.post("/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]})

    reuse = [json.loads(line) for line in capture.lines if "reuse detected" in line]

    assert reuse and reuse[0]["level"] == "WARNING"


def test_no_endpoint_ever_returns_the_password_hash(auth_client: TestClient) -> None:
    reg = register(auth_client)
    pair = register_and_login(auth_client, email="bob@example.com")
    responses = [
        reg,
        login(auth_client),
        auth_client.get("/api/v1/auth/me", headers=bearer(pair["access_token"])),
        auth_client.post("/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]}),
    ]

    for response in responses:
        assert "$2b$" not in response.text
        assert "password_hash" not in response.text
        assert DEFAULT_PASSWORD not in response.text


def test_authentication_errors_are_generic_and_uniform(auth_client: TestClient) -> None:
    register(auth_client)
    bad_inputs = [
        auth_client.get("/api/v1/auth/me"),
        auth_client.get("/api/v1/auth/me", headers=bearer("junk")),
        auth_client.post("/api/v1/auth/refresh", json={"refresh_token": "nope"}),
        login(auth_client, password="Wr0ngPassword1"),
        login(auth_client, email="ghost@example.com"),
    ]

    for response in bad_inputs:
        assert response.status_code == 401
        error = response.json()["error"]
        assert set(error) == {"code", "message", "details"}
        assert error["code"] == "AUTHENTICATION_ERROR"
        assert error["details"] == {}
        for leak in ("signature", "has expired", "jwt", "algorithm", "alice", "ghost", "Traceback"):
            assert leak not in response.text


def test_openapi_declares_bearer_authentication_on_protected_routes(
    auth_client: TestClient,
) -> None:
    schema = auth_client.get("/openapi.json").json()

    assert schema["components"]["securitySchemes"]["BearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
        "description": "Access token returned by POST /api/v1/auth/login.",
    }
    for path, method in (("/api/v1/auth/me", "get"), ("/api/v1/auth/logout", "post")):
        assert {"BearerAuth": []} in schema["paths"][path][method]["security"]
    for path in ("/api/v1/auth/login", "/api/v1/auth/register", "/api/v1/auth/refresh"):
        assert "security" not in schema["paths"][path]["post"]


def test_openapi_documents_error_responses(auth_client: TestClient) -> None:
    schema = auth_client.get("/openapi.json").json()

    login_responses = schema["paths"]["/api/v1/auth/login"]["post"]["responses"]
    assert {"200", "401", "422", "429"} <= set(login_responses)


def test_expired_session_does_not_expose_user_state(
    auth_client: TestClient, migrated_database_url: str
) -> None:
    register(auth_client)

    assert db_scalar(migrated_database_url, "SELECT count(*) FROM users") == 1
    response = auth_client.get("/api/v1/auth/me", headers=bearer("a.b.c"))

    assert response.status_code == 401
    assert "alice" not in response.text
