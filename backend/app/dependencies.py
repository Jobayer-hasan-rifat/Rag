from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.repositories.chunk_repository import ChunkRepository
from app.db.repositories.collection_repository import CollectionRepository
from app.db.repositories.document_repository import DocumentRepository
from app.db.repositories.refresh_token_repository import RefreshTokenRepository
from app.db.repositories.user_repository import RoleRepository, UserRepository
from app.db.session import session_scope
from app.exceptions import AuthenticationError, AuthorizationError
from app.models.user import User
from app.security.authorization import RoleName
from app.security.jwt import JWTService
from app.security.password import PasswordHasher
from app.security.rate_limit import RateLimiter
from app.security.token_denylist import TokenDenylist
from app.services.auth_service import AuthContext, AuthService
from app.services.collection_service import CollectionService
from app.services.document_service import DocumentService
from app.services.health_service import HealthService
from app.services.processing_queue import ProcessingQueue
from app.storage.base import StorageProvider

bearer_scheme = HTTPBearer(
    scheme_name="BearerAuth",
    auto_error=False,
    description="Access token returned by POST /api/v1/auth/login.",
)


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


SettingsDep = Annotated[Settings, Depends(get_app_settings)]


def get_engine(request: Request) -> AsyncEngine:
    engine: AsyncEngine = request.app.state.engine
    return engine


def get_redis(request: Request) -> Redis:
    client: Redis = request.app.state.redis
    return client


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async for session in session_scope(factory):
        yield session


def get_client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


ClientIp = Annotated[str, Depends(get_client_ip)]


def get_health_service(
    engine: Annotated[AsyncEngine, Depends(get_engine)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> HealthService:
    return HealthService(engine=engine, redis=redis)


def get_rate_limiter(
    redis: Annotated[Redis, Depends(get_redis)], settings: SettingsDep
) -> RateLimiter:
    return RateLimiter(
        redis,
        enabled=settings.rate_limit_enabled,
        max_attempts=settings.rate_limit_auth_attempts,
        window_seconds=settings.rate_limit_window_seconds,
    )


LimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]


def get_storage(request: Request) -> StorageProvider:
    storage: StorageProvider = request.app.state.storage
    return storage


def get_processing_queue(request: Request) -> ProcessingQueue:
    queue: ProcessingQueue = request.app.state.processing_queue
    return queue


def get_auth_service(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    redis: Annotated[Redis, Depends(get_redis)],
    limiter: LimiterDep,
    settings: SettingsDep,
) -> AuthService:
    hasher: PasswordHasher = request.app.state.password_hasher
    jwt_service: JWTService = request.app.state.jwt_service
    return AuthService(
        session=session,
        users=UserRepository(session),
        roles=RoleRepository(session),
        refresh_tokens=RefreshTokenRepository(session),
        hasher=hasher,
        jwt_service=jwt_service,
        denylist=TokenDenylist(redis),
        limiter=limiter,
        settings=settings,
    )


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
BearerCredentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]


async def get_auth_context(credentials: BearerCredentials, service: AuthServiceDep) -> AuthContext:
    if credentials is None:
        raise AuthenticationError("Authentication required")
    return await service.authenticate(credentials.credentials)


async def get_optional_auth_context(
    credentials: BearerCredentials, service: AuthServiceDep
) -> AuthContext | None:
    """Anonymous callers get None; a presented-but-invalid token is still rejected."""
    if credentials is None:
        return None
    return await service.authenticate(credentials.credentials)


AuthContextDep = Annotated[AuthContext, Depends(get_auth_context)]


async def get_current_user(context: AuthContextDep) -> User:
    return context.user


async def get_optional_user(
    context: Annotated[AuthContext | None, Depends(get_optional_auth_context)],
) -> User | None:
    return context.user if context else None


CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[User | None, Depends(get_optional_user)]


def require_roles(*allowed: RoleName) -> Callable[[User], Awaitable[User]]:
    async def dependency(user: CurrentUser) -> User:
        if user.role_name not in allowed:
            raise AuthorizationError()
        return user

    return dependency


AdminUser = Annotated[User, Depends(require_roles(RoleName.ADMIN))]


def get_collection_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> CollectionService:
    return CollectionService(session=session, collections=CollectionRepository(session))


def get_document_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    storage: Annotated[StorageProvider, Depends(get_storage)],
    processing_queue: Annotated[ProcessingQueue, Depends(get_processing_queue)],
    settings: SettingsDep,
) -> DocumentService:
    return DocumentService(
        session=session,
        documents=DocumentRepository(session),
        collections=CollectionRepository(session),
        chunks=ChunkRepository(session),
        storage=storage,
        processing_queue=processing_queue,
        settings=settings,
    )


CollectionServiceDep = Annotated[CollectionService, Depends(get_collection_service)]
DocumentServiceDep = Annotated[DocumentService, Depends(get_document_service)]


async def enforce_upload_rate_limit(
    user: CurrentUser, limiter: LimiterDep, settings: SettingsDep
) -> None:
    """Runs before the request body is parsed, so throttled clients cost almost nothing."""
    await limiter.check(
        "upload:user", str(user.id), max_attempts=settings.rate_limit_upload_attempts
    )


async def enforce_retry_rate_limit(
    user: CurrentUser, limiter: LimiterDep, settings: SettingsDep
) -> None:
    await limiter.check(
        "retry:user", str(user.id), max_attempts=settings.rate_limit_upload_attempts
    )
