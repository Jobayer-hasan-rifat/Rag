import asyncio
import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.repositories.lexical_repository import LexicalCandidate, LexicalRepository
from app.db.session import create_engine
from app.retrieval.lexical_query import parse_lexical_query
from tests.files import upload_ok
from tests.processing_helpers import embed_now, process_now


def ready_doc(
    client: TestClient,
    headers: dict[str, str],
    text: str | bytes,
    name: str = "d.txt",
    collection_ids: list[str] | None = None,
) -> str:
    """Upload, process and embed a document so it is searchable (`ready`)."""
    data = text.encode() if isinstance(text, str) else text
    uploaded = upload_ok(
        client,
        headers,
        filename=name,
        content=data,
        content_type=None,
        collection_ids=collection_ids,
    )
    doc_id = str(uploaded["id"])
    assert process_now(client, doc_id).status == "chunked"
    assert embed_now(client, doc_id).status == "ready"
    return doc_id


def user_id_of(client: TestClient, headers: dict[str, str]) -> uuid.UUID:
    return uuid.UUID(client.get("/api/v1/auth/me", headers=headers).json()["data"]["id"])


def lexical_candidates(
    client: TestClient,
    user: uuid.UUID,
    query: str,
    *,
    limit: int = 50,
    max_terms: int = 12,
    document_ids: list[uuid.UUID] | None = None,
    collection_id: uuid.UUID | None = None,
    stopwords: frozenset[str] = frozenset(),
) -> list[LexicalCandidate]:
    """Run the lexical retriever alone, exactly as the hybrid service calls it."""
    settings = client.app.state.settings  # type: ignore[attr-defined]

    async def run() -> list[LexicalCandidate]:
        engine = create_engine(settings)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                return await LexicalRepository(session).candidates(
                    user_id=user,
                    query=parse_lexical_query(query),
                    limit=limit,
                    max_terms=max_terms,
                    document_ids=document_ids,
                    collection_id=collection_id,
                    stopwords=stopwords,
                )
        finally:
            await engine.dispose()

    return asyncio.run(run())


def chunk_texts(db_url: str, candidates: list[LexicalCandidate]) -> list[str]:
    from tests.helpers import db_rows

    if not candidates:
        return []
    texts = {
        str(r[0]): r[1]
        for r in db_rows(
            db_url,
            "SELECT id::text, text FROM document_chunks WHERE id = ANY(CAST(:ids AS uuid[]))",
            ids=[str(c.chunk_id) for c in candidates],
        )
    }
    return [texts[str(c.chunk_id)] for c in candidates]


def only(items: list[Any]) -> Any:
    assert len(items) == 1, items
    return items[0]
