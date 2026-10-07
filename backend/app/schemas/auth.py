import re
import unicodedata
import uuid
from datetime import datetime
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
)

from app.models.user import User
from app.security.password import MAX_PASSWORD_BYTES

MIN_PASSWORD_LENGTH = 8


def _strip(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


def _lowercase(value: str) -> str:
    return value.lower()


def _validate_password_policy(password: str) -> str:
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError(f"Password must be at most {MAX_PASSWORD_BYTES} bytes")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    if not re.search(r"[a-z]", password):
        raise ValueError("Password must contain a lowercase letter")
    if not re.search(r"[A-Z]", password):
        raise ValueError("Password must contain an uppercase letter")
    if not re.search(r"\d", password):
        raise ValueError("Password must contain a digit")
    return password


def _validate_display_name(name: str) -> str:
    if any(unicodedata.category(char).startswith("C") for char in name):
        raise ValueError("Display name must not contain control characters")
    return name


NormalizedEmail = Annotated[
    EmailStr, BeforeValidator(_strip), AfterValidator(_lowercase), Field(max_length=255)
]
NewPassword = Annotated[str, AfterValidator(_validate_password_policy)]
DisplayName = Annotated[
    str,
    BeforeValidator(_strip),
    StringConstraints(min_length=1, max_length=100),
    AfterValidator(_validate_display_name),
]
PresentedPassword = Annotated[str, Field(min_length=1, max_length=256)]
OpaqueToken = Annotated[str, Field(min_length=1, max_length=512)]


class StrictRequest(BaseModel):
    """Unknown fields are rejected so clients cannot smuggle in fields such as `role`."""

    model_config = ConfigDict(extra="forbid")


class RegisterRequest(StrictRequest):
    email: NormalizedEmail
    password: NewPassword
    display_name: DisplayName


class LoginRequest(StrictRequest):
    email: NormalizedEmail
    password: PresentedPassword


class RefreshRequest(StrictRequest):
    refresh_token: OpaqueToken


class LogoutRequest(StrictRequest):
    refresh_token: OpaqueToken


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 (OAuth token type, not a secret)
    expires_in: int


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    role: str
    is_active: bool
    created_at: datetime

    @classmethod
    def from_user(cls, user: User) -> "UserResponse":
        return cls(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            role=user.role_name,
            is_active=user.is_active,
            created_at=user.created_at,
        )
