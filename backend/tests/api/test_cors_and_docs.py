from fastapi.testclient import TestClient


def test_allowed_origin_receives_cors_header(offline_client: TestClient) -> None:
    response = offline_client.get("/health/live", headers={"Origin": "http://localhost:5173"})

    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "x-request-id" in response.headers["access-control-expose-headers"].lower()


def test_unlisted_origin_receives_no_cors_header(offline_client: TestClient) -> None:
    response = offline_client.get("/health/live", headers={"Origin": "https://evil.example"})

    assert "access-control-allow-origin" not in response.headers


def test_preflight_for_allowed_origin_succeeds(offline_client: TestClient) -> None:
    response = offline_client.options(
        "/api/v1/health/live",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_openapi_documents_versioned_routes_only(offline_client: TestClient) -> None:
    schema = offline_client.get("/openapi.json").json()

    assert schema["info"]["title"] == "RAG-Platform"
    assert set(schema["paths"]) == {
        "/api/v1/health",
        "/api/v1/health/live",
        "/api/v1/health/ready",
        "/api/v1/auth/register",
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/api/v1/auth/logout",
        "/api/v1/auth/me",
        "/api/v1/collections",
        "/api/v1/collections/{collection_id}",
        "/api/v1/collections/{collection_id}/documents",
        "/api/v1/collections/{collection_id}/documents/{document_id}",
        "/api/v1/documents",
        "/api/v1/documents/{document_id}",
        "/api/v1/documents/{document_id}/download",
    }


def test_interactive_docs_are_served(offline_client: TestClient) -> None:
    assert offline_client.get("/docs").status_code == 200
    assert offline_client.get("/redoc").status_code == 200
