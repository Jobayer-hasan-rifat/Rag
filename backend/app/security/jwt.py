import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from app.config import Settings

ACCESS_TOKEN_TYPE = "access"  # noqa: S105 (claim value, not a secret)
_REQUIRED_CLAIMS = ["sub", "jti", "iat", "exp", "iss", "aud", "type"]


class InvalidTokenError(Exception):
    """Raised for any token that fails validation; the reason is deliberately not exposed."""


@dataclass(frozen=True)
class AccessToken:
    token: str
    jti: str
    expires_at: datetime


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    jti: str
    expires_at: datetime


class JWTService:
    """Issues and validates access tokens. Tokens carry identity only; roles live in the DB."""

    def __init__(self, settings: Settings) -> None:
        self._key = settings.jwt_secret_key.get_secret_value()
        self._algorithm = settings.jwt_algorithm
        self._issuer = settings.jwt_issuer
        self._audience = settings.jwt_audience
        self._lifetime = timedelta(minutes=settings.access_token_expire_minutes)

    @property
    def lifetime_seconds(self) -> int:
        return int(self._lifetime.total_seconds())

    def create_access_token(
        self, user_id: uuid.UUID, *, now: datetime | None = None
    ) -> AccessToken:
        issued_at = now or datetime.now(UTC)
        expires_at = issued_at + self._lifetime
        jti = uuid.uuid4().hex
        payload = {
            "sub": str(user_id),
            "jti": jti,
            "type": ACCESS_TOKEN_TYPE,
            "iss": self._issuer,
            "aud": self._audience,
            "iat": int(issued_at.timestamp()),
            "exp": int(expires_at.timestamp()),
        }
        token = jwt.encode(payload, self._key, algorithm=self._algorithm)
        return AccessToken(token=token, jti=jti, expires_at=expires_at)

    def decode_access_token(self, token: str) -> AccessClaims:
        try:
            claims = jwt.decode(
                token,
                self._key,
                algorithms=[self._algorithm],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": _REQUIRED_CLAIMS},
            )
            if claims["type"] != ACCESS_TOKEN_TYPE:
                raise InvalidTokenError("wrong token type")
            return AccessClaims(
                user_id=uuid.UUID(claims["sub"]),
                jti=str(claims["jti"]),
                expires_at=datetime.fromtimestamp(claims["exp"], UTC),
            )
        except InvalidTokenError:
            raise
        except (jwt.PyJWTError, ValueError, TypeError, KeyError) as error:
            raise InvalidTokenError("token validation failed") from error
