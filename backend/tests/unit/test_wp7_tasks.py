from __future__ import annotations

from backend.app.tasks.celery_app import celery_app


def test_wp7_tasks_are_registered_under_their_published_names() -> None:
    # `UsageRecorder` enqueues by name; a typo here fails silently at runtime.
    import backend.app.tasks.wp7  # noqa: F401  (registers the tasks)

    assert {
        "wp7.evaluate_budget_attempt",
        "wp7.reconcile_budgets",
        "wp7.sweep_stale_usage",
        "wp7.run_cross_check",
        "wp7.sweep_cross_checks",
    } <= set(celery_app.tasks)


def test_wp7_module_is_included_so_workers_import_it() -> None:
    assert "backend.app.tasks.wp7" in celery_app.conf.include


def test_wp7_periodic_schedules_are_configured() -> None:
    schedule = celery_app.conf.beat_schedule

    assert schedule["wp7-reconcile-budgets"]["task"] == "wp7.reconcile_budgets"
    assert schedule["wp7-reconcile-budgets"]["schedule"] == 300.0
    assert schedule["wp7-sweep-stale-usage"]["task"] == "wp7.sweep_stale_usage"
    assert schedule["wp7-sweep-stale-usage"]["schedule"] == 60.0
    assert schedule["wp7-sweep-cross-checks"]["task"] == "wp7.sweep_cross_checks"
    # The pre-existing ingestion sweep must survive the WP7 additions.
    assert schedule["ingestion-sweep"]["task"] == "ingest.sweep"
