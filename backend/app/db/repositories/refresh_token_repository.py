import uuid
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.refresh_token import RefreshToken


class RefreshTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self, *, user_id: uuid.UUID, family_id: uuid.UUID, token_hash: str, expires_at: datetime
    ) -> RefreshToken:
        record = RefreshToken(
            user_id=user_id, family_id=family_id, token_hash=token_hash, expires_at=expires_at
        )
        self._session.add(record)
        await self._session.flush()
        return record

    async def get_by_hash_for_update(self, token_hash: str) -> RefreshToken | None:
        """Lock the row so concurrent refreshes of one token are serialised."""
        result = await self._session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
        )
        return result.scalar_one_or_none()

    async def revoke_family(self, family_id: uuid.UUID, now: datetime) -> None:
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )

    async def delete_expired(self, before: datetime) -> int:
        """Remove tokens that expired before `before`. An expired token can no longer be used,
        so its row has no security value; revoked rows are kept until they would have expired,
        because replaying one must still be recognised as reuse and end its family."""
        result = await self._session.execute(
            delete(RefreshToken).where(RefreshToken.expires_at < before)
        )
        return int(result.rowcount or 0)  # type: ignore[attr-defined]
