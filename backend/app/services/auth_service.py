import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from redis.exceptions import RedisError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.repositories.refresh_token_repository import RefreshTokenRepository
from app.db.repositories.user_repository import RoleRepository, UserRepository
from app.exceptions import (
    AuthenticationError,
    ConflictError,
    ServiceUnavailableError,
)
from app.models.user import User
from app.observability.logging import get_logger
from app.schemas.auth import TokenPair
from app.security.authorization import RoleName
from app.security.jwt import AccessClaims, InvalidTokenError, JWTService
from app.security.password import PasswordHasher
from app.security.rate_limit import RateLimiter
from app.security.refresh_tokens import generate_refresh_token, hash_refresh_token
from app.security.token_denylist import TokenDenylist

logger = get_logger("app.auth")

ACCOUNT_BUDGET_MULTIPLIER = 5
INVALID_CREDENTIALS = "Invalid email or password"
INVALID_TOKEN = "Invalid or expired token"  # noqa: S105 (error text, not a credential)


@dataclass(frozen=True)
class AuthContext:
    user: User
    claims: AccessClaims


class AuthService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        users: UserRepository,
        roles: RoleRepository,
        refresh_tokens: RefreshTokenRepository,
        hasher: PasswordHasher,
        jwt_service: JWTService,
        denylist: TokenDenylist,
        limiter: RateLimiter,
        settings: Settings,
    ) -> None:
        self._session = session
        self._users = users
        self._roles = roles
        self._refresh_tokens = refresh_tokens
        self._hasher = hasher
        self._jwt = jwt_service
        self._denylist = denylist
        self._limiter = limiter
        self._refresh_lifetime = timedelta(days=settings.refresh_token_expire_days)

    async def register(
        self, *, email: str, password: str, display_name: str, client_ip: str
    ) -> User:
        await self._limiter.check("register:ip", client_ip)
        password_hash = await self._hasher.hash(password)  # always hash: equalises timing
        role = await self._roles.get_by_name(RoleName.USER)
        if role is None:
            raise RuntimeError("default role is missing; run database migrations")
        user = User(
            email=email, display_name=display_name, password_hash=password_hash, role_id=role.id
        )
        try:
            await self._users.add(user)
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            logger.info("registration rejected", extra={"reason": "duplicate_email"})
            raise ConflictError("Unable to register with the provided details") from None
        logger.info("user registered", extra={"user_id": str(user.id)})
        return user

    async def login(self, *, email: str, password: str, client_ip: str) -> TokenPair:
        await self._limiter.check("login:ip", client_ip)
        # A larger budget than per-IP, so one IP cannot lock a victim out of their own account.
        await self._limiter.check("login:email", email, budget_multiplier=ACCOUNT_BUDGET_MULTIPLIER)
        user = await self._users.get_by_email(email)
        password_ok = await self._hasher.verify(password, user.password_hash if user else None)
        if user is None or not password_ok:
            logger.info("login failed", extra={"reason": "invalid_credentials"})
            raise AuthenticationError(INVALID_CREDENTIALS)
        if not user.is_active:
            logger.info("login failed", extra={"reason": "inactive", "user_id": str(user.id)})
            raise AuthenticationError(INVALID_CREDENTIALS)
        pair = await self._issue_token_pair(user, family_id=uuid.uuid4())
        await self._session.commit()
        logger.info("login succeeded", extra={"user_id": str(user.id)})
        return pair

    async def refresh(self, *, refresh_token: str, client_ip: str) -> TokenPair:
        await self._limiter.check("refresh:ip", client_ip)
        now = datetime.now(UTC)
        record = await self._refresh_tokens.get_by_hash_for_update(
            hash_refresh_token(refresh_token)
        )
        if record is None:
            raise AuthenticationError(INVALID_TOKEN)
        if record.revoked_at is not None:
            await self._refresh_tokens.revoke_family(record.family_id, now)
            await self._session.commit()
            logger.warning(
                "refresh token reuse detected; token family revoked",
                extra={"user_id": str(record.user_id), "family_id": str(record.family_id)},
            )
            raise AuthenticationError(INVALID_TOKEN)
        if record.expires_at <= now:
            raise AuthenticationError(INVALID_TOKEN)
        user = await self._users.get_active_record_by_id(record.user_id)
        if user is None or not user.is_active:
            await self._refresh_tokens.revoke_family(record.family_id, now)
            await self._session.commit()
            raise AuthenticationError(INVALID_TOKEN)

        record.revoked_at = now
        pair = await self._issue_token_pair(user, family_id=record.family_id)
        await self._session.commit()
        return pair

    async def logout(self, *, context: AuthContext, refresh_token: str) -> None:
        """Revoke the caller's refresh-token family and the presented access token."""
        record = await self._refresh_tokens.get_by_hash_for_update(
            hash_refresh_token(refresh_token)
        )
        if record is not None and record.user_id == context.user.id:
            await self._refresh_tokens.revoke_family(record.family_id, datetime.now(UTC))
        await self._session.commit()
        try:
            await self._denylist.revoke(context.claims.jti, context.claims.expires_at)
        except RedisError:
            logger.error("could not record access token revocation")
            raise ServiceUnavailableError() from None
        logger.info("logout", extra={"user_id": str(context.user.id)})

    async def authenticate(self, access_token: str) -> AuthContext:
        try:
            claims = self._jwt.decode_access_token(access_token)
        except InvalidTokenError:
            raise AuthenticationError(INVALID_TOKEN) from None
        try:
            revoked = await self._denylist.is_revoked(claims.jti)
        except RedisError:
            logger.error("token denylist unavailable; rejecting request")
            raise ServiceUnavailableError() from None
        if revoked:
            raise AuthenticationError(INVALID_TOKEN)
        user = await self._users.get_active_record_by_id(claims.user_id)
        if user is None or not user.is_active:
            raise AuthenticationError(INVALID_TOKEN)
        return AuthContext(user=user, claims=claims)

    async def _issue_token_pair(self, user: User, *, family_id: uuid.UUID) -> TokenPair:
        access = self._jwt.create_access_token(user.id)
        refresh_token = generate_refresh_token()
        await self._refresh_tokens.add(
            user_id=user.id,
            family_id=family_id,
            token_hash=hash_refresh_token(refresh_token),
            expires_at=datetime.now(UTC) + self._refresh_lifetime,
        )
        return TokenPair(
            access_token=access.token,
            refresh_token=refresh_token,
            expires_in=self._jwt.lifetime_seconds,
        )
