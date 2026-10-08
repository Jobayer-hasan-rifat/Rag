import hashlib
import uuid
from dataclasses import dataclass

from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.chunking.types import ChunkDraft
from app.models.document_chunk import DocumentChunk
from app.models.document_section import DocumentSection

_INSERT_BATCH = 1000


@dataclass(frozen=True)
class ChunkRow:
    """A draft bound to the persisted section it came from."""

    draft: ChunkDraft
    section_id: uuid.UUID


class ChunkRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def replace_all(self, document_id: uuid.UUID, version: str, rows: list[ChunkRow]) -> None:
        """Replace a document's chunks in the caller's transaction (idempotent, no duplicates)."""
        await self._session.execute(
            delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
        )
        for start in range(0, len(rows), _INSERT_BATCH):
            await self._session.execute(
                insert(DocumentChunk),
                [
                    {
                        "document_id": document_id,
                        "section_id": row.section_id,
                        "chunking_version": version,
                        "chunk_index": row.draft.chunk_index,
                        "text": row.draft.text,
                        "char_count": len(row.draft.text),
                        "text_sha256": hashlib.sha256(row.draft.text.encode("utf-8")).hexdigest(),
                        "start_char": row.draft.start_char,
                        "end_char": row.draft.end_char,
                        "overlap_chars": row.draft.overlap_chars,
                        "page_number": row.draft.page_number,
                        "heading": row.draft.heading,
                        "heading_level": row.draft.heading_level,
                        "heading_path": list(row.draft.heading_path),
                    }
                    for row in rows[start : start + _INSERT_BATCH]
                ],
            )

    async def list_page(
        self, document_id: uuid.UUID, *, page: int, page_size: int
    ) -> tuple[list[tuple[DocumentChunk, int]], int]:
        """Chunks in reading order with their section's ordinal (for inspection)."""
        total = (
            await self._session.execute(
                select(func.count())
                .select_from(DocumentChunk)
                .where(DocumentChunk.document_id == document_id)
            )
        ).scalar_one()
        result = await self._session.execute(
            select(DocumentChunk, DocumentSection.ordinal)
            .join(DocumentSection, DocumentSection.id == DocumentChunk.section_id)
            .where(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.chunk_index)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return [(row[0], row[1]) for row in result.all()], total
