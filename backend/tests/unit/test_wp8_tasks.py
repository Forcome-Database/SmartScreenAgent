from __future__ import annotations

from backend.app.tasks.celery_app import celery_app


def test_wp8_tasks_are_registered_under_their_published_names() -> None:
    import backend.app.tasks.wp8  # noqa: F401

    assert {"sync.pull_dingtalk", "sync.replay_failed"} <= set(celery_app.tasks)


def test_sync_is_not_scheduled_while_disabled() -> None:
    # The kill switch must remove the schedule entry, not merely make the task
    # return early: a registered schedule still wakes a worker every interval.
    from backend.app.config import get_settings

    assert get_settings().DINGTALK_SYNC_ENABLED is False
    assert "wp8-pull-dingtalk" not in celery_app.conf.beat_schedule
    assert "wp8-replay-failed" not in celery_app.conf.beat_schedule


def test_no_scheduled_task_names_a_wp8_task_while_disabled() -> None:
    """Absent by key is not enough — assert on what would actually be dispatched.

    A future edit could reintroduce the entry under a different key and the
    key-based assertion above would still pass.
    """
    scheduled = {entry["task"] for entry in celery_app.conf.beat_schedule.values()}

    assert "sync.pull_dingtalk" not in scheduled
    assert "sync.replay_failed" not in scheduled


def test_the_wp7_schedules_survive() -> None:
    schedule = celery_app.conf.beat_schedule

    assert schedule["ingestion-sweep"]["task"] == "ingest.sweep"
    assert schedule["wp7-reconcile-budgets"]["task"] == "wp7.reconcile_budgets"


def test_the_wp8_task_module_is_included_for_the_worker() -> None:
    # Beat dispatches by name; a worker that never imports the module has no
    # task registered to receive it.
    assert "backend.app.tasks.wp8" in celery_app.conf.include
