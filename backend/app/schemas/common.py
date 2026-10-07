from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class Meta(BaseModel):
    request_id: str | None
    timestamp: datetime


class ResponseEnvelope[DataT](BaseModel):
    data: DataT
    meta: Meta


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorBody
    meta: Meta
