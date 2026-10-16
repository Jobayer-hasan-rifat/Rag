import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.auth import StrictRequest


class CollectionSummary(BaseModel):
    id: uuid.UUID
    name: str


class VersionSummary(BaseModel):
    id: uuid.UUID
    version_number: int
    status: str
    failure_reason: str | None


class VersionResponse(BaseModel):
    """One version of a document. Never includes storage keys or filesystem details."""

    id: uuid.UUID = Field(description="The version id")
    document_id: uuid.UUID
    version_number: int
    filename: str
    file_type: str
    content_type: str
    file_size: int
    checksum_sha256: str
    status: str
    error_message: str | None
    failure_reason: str | None
    is_active: bool = Field(description="The version search and answers use")
    is_current: bool = Field(description="The version the document is shown as")
    searchable: bool = Field(description="Active and ready")
    chunk_count: int | None
    embedded_chunk_count: int | None
    processing_started_at: datetime | None
    processing_completed_at: datetime | None
    embedding_completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class VersionUploadResponse(BaseModel):
    version: VersionResponse
    created: bool = Field(
        description="False if identical content was already a version of this document; that "
        "version is returned and nothing new is created"
    )


class DocumentResponse(BaseModel):
    """Safe document metadata. Never includes storage keys or filesystem details.

    The fields describe the document's current version: the active one, or the newest while
    nothing is active yet."""

    id: uuid.UUID = Field(description="The document id (stable across versions)")
    version_id: uuid.UUID
    version_number: int
    version_count: int
    active_version_id: uuid.UUID | None = Field(
        description="The searchable version, null until one is ready"
    )
    latest_version: VersionSummary | None = Field(
        description="The newest version when it differs from the current one (for example a "
        "replacement that is still processing or has failed)"
    )
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
    embedded_chunk_count: int | None
    embedding_completed_at: datetime | None
    searchable: bool
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
