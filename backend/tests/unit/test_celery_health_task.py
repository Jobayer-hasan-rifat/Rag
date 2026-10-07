from app.workers.health_tasks import health_check_task


def test_health_check_task_returns_ok() -> None:
    result = health_check_task.apply()

    assert result.successful()
    assert result.get() == {"status": "ok"}
