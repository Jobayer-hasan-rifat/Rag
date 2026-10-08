import uuid
from datetime import datetime

from pydantic import BaseModel

from app.schemas.auth import StrictRequest


class CollectionSummary(BaseModel):
    id: uuid.UUID
    name: str


class DocumentResponse(BaseModel):
    """Safe document metadata. Never includes storage keys or filesystem details."""

    id: uuid.UUID
    filename: str
    file_type: str
    content_type: str
    file_size: int
    checksum_sha256: str
    status: str
    error_message: str | None
    failure_reason: str | None
    processing_started_at: datetime | None
    processing_completed_at: datetime | None
    page_count: int | None
    character_count: int | None
    chunk_count: int | None
    chunking_version: str | None
    collections: list[CollectionSummary]
    created_at: datetime
    updated_at: datetime


class DocumentUpdate(StrictRequest):
    filename: str


class ChunkResponse(BaseModel):
    """A chunk with its source location. For inspection and, later, citations."""

    id: uuid.UUID
    chunk_index: int
    text: str
    char_count: int
    section_ordinal: int
    page_number: int | None
    heading: str | None
    heading_level: int | None
    heading_path: list[str]
    start_char: int
    end_char: int
    overlap_chars: int
