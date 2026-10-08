import uuid
from dataclasses import dataclass

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document_section import DocumentSection

_INSERT_BATCH = 500


@dataclass(frozen=True)
class NewSection:
    kind: str
    text: str
    page_number: int | None = None
    heading: str | None = None
    heading_level: int | None = None


class SectionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def replace_all(self, document_id: uuid.UUID, sections: list[NewSection]) -> None:
        """Replace a document's content in the caller's transaction (retry-safe: no duplicates)."""
        await self._session.execute(
            delete(DocumentSection).where(DocumentSection.document_id == document_id)
        )
        for start in range(0, len(sections), _INSERT_BATCH):
            batch = sections[start : start + _INSERT_BATCH]
            await self._session.execute(
                insert(DocumentSection),
                [
                    {
                        "document_id": document_id,
                        "ordinal": start + offset,
                        "kind": section.kind,
                        "page_number": section.page_number,
                        "heading": section.heading,
                        "heading_level": section.heading_level,
                        "text": section.text,
                        "char_count": len(section.text),
                    }
                    for offset, section in enumerate(batch)
                ],
            )

    async def load_for_chunking(self, document_id: uuid.UUID) -> list[DocumentSection]:
        """All sections of a document in reading order."""
        result = await self._session.execute(
            select(DocumentSection)
            .where(DocumentSection.document_id == document_id)
            .order_by(DocumentSection.ordinal)
        )
        return list(result.scalars().all())
