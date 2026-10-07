from celery import Celery

from app.config import Settings, get_settings
from app.observability.logging import configure_logging


def create_celery_app(settings: Settings | None = None) -> Celery:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.app_env)

    celery = Celery(
        "rag",
        broker=settings.celery_broker_url,
        backend=settings.celery_result_backend,
        include=["app.workers.health_tasks"],
    )
    celery.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        task_acks_late=True,
        task_track_started=True,
        worker_prefetch_multiplier=1,
        worker_hijack_root_logger=False,
        worker_redirect_stdouts=False,
        broker_connection_retry_on_startup=True,
        result_expires=3600,
    )
    return celery


celery_app = create_celery_app()
