import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest

from app.security.jwt import InvalidTokenError, JWTService
from tests.conftest import SettingsFactory

USER_ID = uuid.uuid4()


@pytest.fixture
def service(make_settings: SettingsFactory) -> JWTService:
    return JWTService(make_settings())


def _claims(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": str(USER_ID),
        "jti": uuid.uuid4().hex,
        "type": "access",
        "iss": "rag-platform",
        "aud": "rag-platform-users",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    claims.update(overrides)
    return {key: value for key, value in claims.items() if value is not None}


def _sign(claims: dict[str, Any], key: str = "test-only-secret-key-0123456789abcdef") -> str:
    return jwt.encode(claims, key, algorithm="HS256")


def test_valid_token_round_trips(service: JWTService) -> None:
    issued = service.create_access_token(USER_ID)

    claims = service.decode_access_token(issued.token)

    assert claims.user_id == USER_ID
    assert claims.jti == issued.jti
    assert claims.expires_at.replace(microsecond=0) == issued.expires_at.replace(microsecond=0)


def test_token_contains_only_identity_claims(service: JWTService) -> None:
    payload = jwt.decode(
        service.create_access_token(USER_ID).token, options={"verify_signature": False}
    )

    assert set(payload) == {"sub", "jti", "type", "iss", "aud", "iat", "exp"}


def test_lifetime_follows_configuration(make_settings: SettingsFactory) -> None:
    service = JWTService(make_settings(access_token_expire_minutes=5))
    issued = service.create_access_token(USER_ID)

    assert service.lifetime_seconds == 300
    assert issued.expires_at - datetime.now(UTC) < timedelta(minutes=5, seconds=1)


def test_expired_token_is_rejected(service: JWTService) -> None:
    old = datetime.now(UTC) - timedelta(hours=2)
    token = service.create_access_token(USER_ID, now=old).token

    with pytest.raises(InvalidTokenError):
        service.decode_access_token(token)


def test_token_signed_with_another_key_is_rejected(service: JWTService) -> None:
    forged = _sign(_claims(), key="a-different-secret-key-0123456789abcdef")

    with pytest.raises(InvalidTokenError):
        service.decode_access_token(forged)


@pytest.mark.parametrize("token", ["", "not-a-jwt", "a.b.c", "a.b", "....", "Bearer abc"])
def test_malformed_tokens_are_rejected(service: JWTService, token: str) -> None:
    with pytest.raises(InvalidTokenError):
        service.decode_access_token(token)


@pytest.mark.parametrize("missing", ["sub", "jti", "iat", "exp", "iss", "aud", "type"])
def test_tokens_missing_a_required_claim_are_rejected(service: JWTService, missing: str) -> None:
    token = _sign(_claims(**{missing: None}))

    with pytest.raises(InvalidTokenError):
        service.decode_access_token(token)


def test_wrong_token_type_is_rejected(service: JWTService) -> None:
    with pytest.raises(InvalidTokenError):
        service.decode_access_token(_sign(_claims(type="refresh")))


def test_wrong_audience_and_issuer_are_rejected(service: JWTService) -> None:
    with pytest.raises(InvalidTokenError):
        service.decode_access_token(_sign(_claims(aud="someone-else")))
    with pytest.raises(InvalidTokenError):
        service.decode_access_token(_sign(_claims(iss="someone-else")))


def test_non_uuid_subject_is_rejected(service: JWTService) -> None:
    with pytest.raises(InvalidTokenError):
        service.decode_access_token(_sign(_claims(sub="not-a-uuid")))


def test_unsigned_alg_none_token_is_rejected(service: JWTService) -> None:
    unsigned = jwt.encode(_claims(), key=None, algorithm="none")

    with pytest.raises(InvalidTokenError):
        service.decode_access_token(unsigned)


def test_token_using_a_different_hmac_algorithm_is_rejected(service: JWTService) -> None:
    token = jwt.encode(_claims(), "test-only-secret-key-0123456789abcdef", algorithm="HS512")

    with pytest.raises(InvalidTokenError):
        service.decode_access_token(token)


def test_each_token_has_a_unique_id(service: JWTService) -> None:
    assert service.create_access_token(USER_ID).jti != service.create_access_token(USER_ID).jti
