import re
import uuid

from fastapi.testclient import TestClient


def test_request_id_is_generated_when_absent(offline_client: TestClient) -> None:
    response = offline_client.get("/health/live")

    generated = response.headers["x-request-id"]
    assert uuid.UUID(generated).version == 4


def test_well_formed_client_request_id_is_propagated(offline_client: TestClient) -> None:
    response = offline_client.get("/health/live", headers={"X-Request-ID": "trace-abc.123_XYZ"})

    assert response.headers["x-request-id"] == "trace-abc.123_XYZ"
    assert response.json()["meta"]["request_id"] == "trace-abc.123_XYZ"


def test_malformed_client_request_id_is_replaced(offline_client: TestClient) -> None:
    for bad in ["short", "has spaces in it!", "x" * 65, "line\\nbreak-injection"]:
        response = offline_client.get("/health/live", headers={"X-Request-ID": bad})

        assert response.headers["x-request-id"] != bad
        assert re.fullmatch(r"[0-9a-f-]{36}", response.headers["x-request-id"])


def test_every_request_gets_a_distinct_id(offline_client: TestClient) -> None:
    first = offline_client.get("/health/live").headers["x-request-id"]
    second = offline_client.get("/health/live").headers["x-request-id"]

    assert first != second
