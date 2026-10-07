from collections.abc import Iterator

import pytest
from celery import Celery
from celery.contrib.testing.worker import start_worker

from app.config import Settings
from app.workers.celery_app import create_celery_app

pytestmark = pytest.mark.integration


@pytest.fixture
def infra_celery_settings(infra_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Settings:
    # Celery gives these environment variables precedence over app configuration.
    monkeypatch.setenv("CELERY_BROKER_URL", infra_settings.celery_broker_url)
    monkeypatch.setenv("CELERY_RESULT_BACKEND", infra_settings.celery_result_backend)
    return infra_settings


@pytest.fixture
def celery_with_worker(infra_celery_settings: Settings) -> Iterator[Celery]:
    app = create_celery_app(infra_celery_settings)
    with start_worker(app, pool="solo", perform_ping_check=False, loglevel="WARNING"):
        yield app


def test_task_round_trips_through_redis_and_a_real_worker(celery_with_worker: Celery) -> None:
    result = celery_with_worker.send_task("app.workers.health_tasks.health_check_task")

    assert result.get(timeout=30) == {"status": "ok"}


def test_celery_uses_redis_for_broker_and_results(infra_celery_settings: Settings) -> None:
    app = create_celery_app(infra_celery_settings)

    assert app.conf.broker_url == infra_celery_settings.celery_broker_url
    assert app.conf.result_backend == infra_celery_settings.celery_result_backend
    assert app.conf.task_serializer == "json"
    assert app.conf.accept_content == ["json"]
