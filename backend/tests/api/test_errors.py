from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.exceptions import AppError
from app.main import create_app
from tests.conftest import SettingsFactory


class SampleDomainError(AppError):
    status_code = 409
    code = "SAMPLE_CONFLICT"


@pytest.fixture
def client(make_settings: SettingsFactory) -> Iterator[TestClient]:
    app: FastAPI = create_app(make_settings())

    @app.get("/_test/domain-error")
    async def domain_error() -> None:
        raise SampleDomainError("Already exists", details={"field": "name"})

    @app.get("/_test/crash")
    async def crash() -> None:
        raise RuntimeError("db password is hunter2 at /srv/app/secret.py")

    @app.get("/_test/validated")
    async def validated(limit: int) -> dict[str, int]:
        return {"limit": limit}

    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def test_unknown_route_returns_error_envelope(client: TestClient) -> None:
    response = client.get("/nope")

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["meta"]["request_id"] == response.headers["x-request-id"]


def test_wrong_method_returns_error_envelope(client: TestClient) -> None:
    response = client.post("/health/live")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


def test_validation_error_lists_fields(client: TestClient) -> None:
    response = client.get("/_test/validated", params={"limit": "abc"})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"]["fields"][0]["location"] == ["query", "limit"]


def test_application_error_uses_its_status_code_and_details(client: TestClient) -> None:
    response = client.get("/_test/domain-error")

    assert response.status_code == 409
    error = response.json()["error"]
    assert error == {
        "code": "SAMPLE_CONFLICT",
        "message": "Already exists",
        "details": {"field": "name"},
    }


def test_unexpected_error_is_generic_and_leaks_nothing(client: TestClient) -> None:
    response = client.get("/_test/crash")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "hunter2" not in response.text
    assert "secret.py" not in response.text
    assert "Traceback" not in response.text
    assert response.headers["x-request-id"] == response.json()["meta"]["request_id"]
