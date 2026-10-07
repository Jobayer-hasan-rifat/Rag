import uuid
from enum import StrEnum

from app.exceptions import AuthorizationError


class RoleName(StrEnum):
    USER = "user"
    ADMIN = "admin"


def can_access_owned_resource(*, actor_id: uuid.UUID, actor_role: str, owner_id: uuid.UUID) -> bool:
    """Owners may access their own resources; administrators may access any."""
    return actor_id == owner_id or actor_role == RoleName.ADMIN


def require_owner_or_admin(*, actor_id: uuid.UUID, actor_role: str, owner_id: uuid.UUID) -> None:
    if not can_access_owned_resource(actor_id=actor_id, actor_role=actor_role, owner_id=owner_id):
        raise AuthorizationError()
