import re
import uuid

import pytest

from app.exceptions import AuthorizationError
from app.security.authorization import (
    RoleName,
    can_access_owned_resource,
    require_owner_or_admin,
)
from app.security.refresh_tokens import generate_refresh_token, hash_refresh_token


def test_refresh_tokens_are_long_unique_and_url_safe() -> None:
    first, second = generate_refresh_token(), generate_refresh_token()

    assert first != second
    assert len(first) >= 64
    assert re.fullmatch(r"[A-Za-z0-9_-]+", first)


def test_hash_is_deterministic_hex_digest_and_not_the_token() -> None:
    token = generate_refresh_token()

    digest = hash_refresh_token(token)

    assert digest == hash_refresh_token(token)
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert token not in digest


def test_owner_can_access_own_resource() -> None:
    user = uuid.uuid4()

    assert can_access_owned_resource(actor_id=user, actor_role=RoleName.USER, owner_id=user)


def test_other_user_cannot_access_resource() -> None:
    assert not can_access_owned_resource(
        actor_id=uuid.uuid4(), actor_role=RoleName.USER, owner_id=uuid.uuid4()
    )


def test_admin_can_access_any_resource() -> None:
    assert can_access_owned_resource(
        actor_id=uuid.uuid4(), actor_role=RoleName.ADMIN, owner_id=uuid.uuid4()
    )


def test_require_owner_or_admin_raises_for_strangers() -> None:
    with pytest.raises(AuthorizationError):
        require_owner_or_admin(actor_id=uuid.uuid4(), actor_role="user", owner_id=uuid.uuid4())
