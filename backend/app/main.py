from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.error_handlers import register_error_handlers
from app.api.middleware.request_context import RequestContextMiddleware
from app.api.router import api_v1_router, root_health_router
from app.config import Settings, get_settings
from app.db.session import create_engine, create_session_factory
from app.observability.logging import configure_logging, get_logger
from app.redis import create_redis_client

logger = get_logger("app.main")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.app_env)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        redis = create_redis_client(settings)
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        app.state.redis = redis
        logger.info("application started", extra={"version": __version__})
        try:
            yield
        finally:
            await redis.aclose()
            await engine.dispose()
            logger.info("application stopped")

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="Intelligent Document Processing & RAG Platform API.",
        debug=False,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestContextMiddleware)

    app.state.settings = settings
    register_error_handlers(app)
    app.include_router(root_health_router)
    app.include_router(api_v1_router)
    return app


def get_app() -> FastAPI:
    return create_app()
