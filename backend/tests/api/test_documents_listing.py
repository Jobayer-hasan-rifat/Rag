import pytest
from fastapi.testclient import TestClient

from tests.files import (
    DOCX_TYPE,
    create_collection,
    docx_bytes,
    make_user,
    markdown_bytes,
    pdf_bytes,
    text_bytes,
    upload_ok,
)
from tests.helpers import db_execute

pytestmark = pytest.mark.integration

DOCS = "/api/v1/documents"


@pytest.fixture
def library(auth_client: TestClient, migrated_database_url: str) -> dict[str, object]:
    alice = make_user(auth_client, "alice@example.com")
    papers = create_collection(auth_client, alice, "Papers")

    def add(name: str, content: bytes, ctype: str, collections: list[str] | None = None) -> object:
        return upload_ok(
            auth_client,
            alice,
            filename=name,
            content=content,
            content_type=ctype,
            collection_ids=collections,
        )

    items = {
        "alpha.pdf": add("alpha.pdf", pdf_bytes("1"), "application/pdf", [papers]),
        "Beta Notes.txt": add("Beta Notes.txt", text_bytes("2"), "text/plain"),
        "gamma.md": add("gamma.md", markdown_bytes("3"), "text/markdown"),
        "delta.docx": add("delta.docx", docx_bytes("4"), DOCX_TYPE, [papers]),
        "100%_done.pdf": add("100%_done.pdf", pdf_bytes("5"), "application/pdf"),
    }
    return {
        "client": auth_client,
        "alice": alice,
        "papers": papers,
        "items": items,
        "db": migrated_database_url,
    }


def _names(response: object) -> list[str]:
    return [d["filename"] for d in response.json()["data"]]  # type: ignore[attr-defined]


def test_default_listing_is_newest_first_with_pagination_metadata(
    library: dict[str, object],
) -> None:
    client: TestClient = library["client"]  # type: ignore[assignment]

    response = client.get(DOCS, headers=library["alice"])

    body = response.json()
    assert _names(response) == [
        "100%_done.pdf",
        "delta.docx",
        "gamma.md",
        "Beta Notes.txt",
        "alpha.pdf",
    ]
    assert body["meta"]["page"] == 1 and body["meta"]["page_size"] == 20
    assert body["meta"]["total_items"] == 5 and body["meta"]["total_pages"] == 1
    assert body["meta"]["request_id"]
    assert all("storage" not in str(item).lower() for item in body["data"])


def test_pagination_is_stable_and_complete(library: dict[str, object]) -> None:
    client: TestClient = library["client"]  # type: ignore[assignment]
    alice: dict[str, str] = library["alice"]  # type: ignore[assignment]

    pages = [
        client.get(DOCS, headers=alice, params={"page": p, "page_size": 2}) for p in (1, 2, 3, 4)
    ]
    collected = [name for response in pages for name in _names(response)]

    assert [len(_names(p)) for p in pages] == [2, 2, 1, 0]
    assert len(collected) == len(set(collected)) == 5
    assert pages[0].json()["meta"]["total_pages"] == 3


def test_ordering_is_deterministic_when_timestamps_tie(library: dict[str, object]) -> None:
    client: TestClient = library["client"]  # type: ignore[assignment]
    alice: dict[str, str] = library["alice"]  # type: ignore[assignment]
    db_execute(str(library["db"]), "UPDATE documents SET created_at = '2026-01-01T00:00:00Z'")

    first = _names(client.get(DOCS, headers=alice, params={"page_size": 2}))
    first_again = _names(client.get(DOCS, headers=alice, params={"page_size": 2}))
    rest = _names(client.get(DOCS, headers=alice, params={"page_size": 2, "page": 2}))

    assert first == first_again
    assert len(set(first) | set(rest)) == 4


@pytest.mark.parametrize(
    ("sort", "order", "expected_first"),
    [
        ("filename", "asc", "100%_done.pdf"),
        ("filename", "desc", "gamma.md"),
        ("created_at", "asc", "alpha.pdf"),
        ("file_size", "desc", "delta.docx"),
    ],
)
def test_sorting_options(
    library: dict[str, object], sort: str, order: str, expected_first: str
) -> None:
    client: TestClient = library["client"]  # type: ignore[assignment]

    response = client.get(DOCS, headers=library["alice"], params={"sort": sort, "order": order})

    assert _names(response)[0] == expected_first


@pytest.mark.parametrize(
    "params",
    [
        {"sort": "user_id"},
        {"sort": "storage_key"},
        {"order": "sideways"},
        {"status": "deleted"},
        {"file_type": "exe"},
        {"collection_id": "nope"},
    ],
)
def test_invalid_filters_and_sort_fields_are_rejected(
    library: dict[str, object], params: dict[str, str]
) -> None:
    client: TestClient = library["client"]  # type: ignore[assignment]

    assert client.get(DOCS, headers=library["alice"], params=params).status_code == 422


@pytest.mark.parametrize(
    "params",
    [{"page": 0}, {"page": -1}, {"page_size": 0}, {"page_size": 101}, {"page_size": "all"}],
)
def test_invalid_pagination_is_rejected(
    library: dict[str, object], params: dict[str, object]
) -> None:
    client: TestClient = library["client"]  # type: ignore[assignment]

    response = client.get(DOCS, headers=library["alice"], params=params)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_page_size_can_be_tuned_up_to_the_maximum(library: dict[str, object]) -> None:
    client: TestClient = library["client"]  # type: ignore[assignment]

    response = client.get(DOCS, headers=library["alice"], params={"page_size": 100})

    assert response.status_code == 200 and response.json()["meta"]["page_size"] == 100


def test_filter_by_file_type_and_status(library: dict[str, object]) -> None:
    client: TestClient = library["client"]  # type: ignore[assignment]
    alice: dict[str, str] = library["alice"]  # type: ignore[assignment]
    db_execute(
        str(library["db"]), "UPDATE documents SET status = 'ready' WHERE filename = 'alpha.pdf'"
    )

    pdfs = client.get(
        DOCS, headers=alice, params={"file_type": "pdf", "sort": "filename", "order": "asc"}
    )
    ready = client.get(DOCS, headers=alice, params={"status": "ready"})
    pending = client.get(DOCS, headers=alice, params={"status": "pending"})

    assert _names(pdfs) == ["100%_done.pdf", "alpha.pdf"]
    assert _names(ready) == ["alpha.pdf"]
    assert pending.json()["meta"]["total_items"] == 4


def test_filter_by_collection(library: dict[str, object]) -> None:
    client: TestClient = library["client"]  # type: ignore[assignment]

    response = client.get(
        DOCS, headers=library["alice"], params={"collection_id": library["papers"]}
    )

    assert sorted(_names(response)) == ["alpha.pdf", "delta.docx"]
    assert response.json()["meta"]["total_items"] == 2


def test_filename_search_is_case_insensitive_and_escapes_wildcards(
    library: dict[str, object],
) -> None:
    client: TestClient = library["client"]  # type: ignore[assignment]
    alice: dict[str, str] = library["alice"]  # type: ignore[assignment]

    assert _names(client.get(DOCS, headers=alice, params={"search": "BETA"})) == ["Beta Notes.txt"]
    assert _names(client.get(DOCS, headers=alice, params={"search": "%"})) == ["100%_done.pdf"]
    assert _names(client.get(DOCS, headers=alice, params={"search": "_"})) == ["100%_done.pdf"]
    assert _names(client.get(DOCS, headers=alice, params={"search": "nomatch"})) == []
    assert client.get(DOCS, headers=alice, params={"search": "x" * 101}).status_code == 422


def test_listing_includes_collection_membership_without_n_plus_one(
    library: dict[str, object],
) -> None:
    from sqlalchemy import event

    client: TestClient = library["client"]  # type: ignore[assignment]
    alice: dict[str, str] = library["alice"]  # type: ignore[assignment]
    engine = client.app.state.engine  # type: ignore[attr-defined]
    statements: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def capture(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        statements.append(statement)

    response = client.get(DOCS, headers=alice)

    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    by_name = {d["filename"]: d["collections"] for d in response.json()["data"]}
    assert by_name["alpha.pdf"][0]["name"] == "Papers"
    assert by_name["gamma.md"] == []
    assert len(selects) <= 5  # auth, count, page, one batched collections query
