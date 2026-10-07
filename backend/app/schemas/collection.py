import unicodedata
import uuid
from datetime import datetime
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    Field,
    StringConstraints,
    model_validator,
)

from app.schemas.auth import StrictRequest


def _clean_name(value: object) -> object:
    if not isinstance(value, str):
        return value
    text = unicodedata.normalize("NFC", value)
    return " ".join(text.split())


def _no_control_characters(value: str) -> str:
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise ValueError("Must not contain control characters")
    return value


def _blank_to_none(value: object) -> object:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return value


CollectionName = Annotated[
    str,
    BeforeValidator(_clean_name),
    StringConstraints(min_length=1, max_length=255),
    AfterValidator(_no_control_characters),
]


def _limit_description(value: str | None) -> str | None:
    if value is not None and len(value) > MAX_DESCRIPTION_LENGTH:
        raise ValueError(f"Must be at most {MAX_DESCRIPTION_LENGTH} characters")
    return value


MAX_DESCRIPTION_LENGTH = 2000
CollectionDescription = Annotated[
    str | None, BeforeValidator(_blank_to_none), AfterValidator(_limit_description)
]


class CollectionCreate(StrictRequest):
    name: CollectionName
    description: CollectionDescription = None


class CollectionUpdate(StrictRequest):
    name: CollectionName | None = None
    description: CollectionDescription = None

    @model_validator(mode="after")
    def _require_a_change(self) -> "CollectionUpdate":
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("name cannot be null")
        return self


class AddDocumentsRequest(StrictRequest):
    document_ids: Annotated[list[uuid.UUID], Field(min_length=1, max_length=100)]


class AddDocumentsResult(BaseModel):
    added_count: int
    already_exists_count: int


class CollectionResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    document_count: int
    created_at: datetime
    updated_at: datetime
