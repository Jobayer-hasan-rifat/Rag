from celery import Celery

from app.config import Settings, get_settings
from app.observability.logging import configure_logging

PROCESSING_QUEUE = "processing"
PROCESS_TASK = "app.workers.document_tasks.process_document"
RECOVER_TASK = "app.workers.document_tasks.recover_stalled_documents"
RECHUNK_TASK = "app.workers.document_tasks.rechunk_document"


def create_celery_app(settings: Settings | None = None, *, configure_logs: bool = True) -> Celery:
    settings = settings or get_settings()
    if configure_logs:
        configure_logging(settings.log_level, settings.app_env)

    celery = Celery(
        "rag",
        broker=settings.celery_broker_url,
        backend=settings.celery_result_backend,
        include=["app.workers.health_tasks", "app.workers.document_tasks"],
    )
    celery.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        task_track_started=True,
        worker_prefetch_multiplier=1,
        worker_max_tasks_per_child=settings.worker_max_tasks_per_child,
        worker_max_memory_per_child=settings.worker_max_memory_per_child_kb,
        worker_hijack_root_logger=False,
        worker_redirect_stdouts=False,
        broker_connection_retry_on_startup=True,
        broker_connection_timeout=2,
        # Unacknowledged tasks are redelivered after this long; it must outlast the hard limit.
        broker_transport_options={
            "visibility_timeout": settings.processing_hard_time_limit_seconds + 120
        },
        task_publish_retry=True,
        task_publish_retry_policy={
            "max_retries": 1,
            "interval_start": 0,
            "interval_step": 0.2,
            "interval_max": 0.5,
        },
        result_expires=3600,
        task_routes={"app.workers.document_tasks.*": {"queue": PROCESSING_QUEUE}},
        task_annotations={
            PROCESS_TASK: {
                "soft_time_limit": settings.processing_soft_time_limit_seconds,
                "time_limit": settings.processing_hard_time_limit_seconds,
                "max_retries": settings.processing_max_attempts,
            },
            RECHUNK_TASK: {
                "soft_time_limit": settings.processing_soft_time_limit_seconds,
                "time_limit": settings.processing_hard_time_limit_seconds,
            },
            RECOVER_TASK: {"soft_time_limit": 60, "time_limit": 90},
        },
        beat_schedule={
            "recover-stalled-documents": {
                "task": RECOVER_TASK,
                "schedule": float(settings.processing_sweep_interval_seconds),
            }
        },
        rag_settings=settings,
    )
    return celery


celery_app = create_celery_app()
