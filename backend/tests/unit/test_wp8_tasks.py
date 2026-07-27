from __future__ import annotations

from backend.app.config import get_settings
from backend.app.tasks.celery_app import build_beat_schedule, celery_app


def _schedule(*, enabled: bool) -> dict:
    """The schedule these settings imply, with the switch forced either way.

    The suite's own environment can only ever be the disabled one:
    `apply_test_environment` pins `DINGTALK_SYNC_ENABLED=false` and
    `celery_app.py` reads settings at import. Asserting against the pure
    builder is what makes the positive direction testable at all — without it,
    deleting the kill switch entirely would keep every other test in this file
    green.
    """
    return build_beat_schedule(
        get_settings().model_copy(update={"DINGTALK_SYNC_ENABLED": enabled})
    )


def test_wp8_tasks_are_registered_under_their_published_names() -> None:
    import backend.app.tasks.wp8  # noqa: F401

    assert {"sync.pull_dingtalk", "sync.replay_failed", "sync.pull_jds"} <= set(
        celery_app.tasks
    )


def test_the_wp8_tasks_carry_their_own_time_limits() -> None:
    """A full run must be able to outlive the global 600 s limit — and still audit.

    `SYNC_MAX_ITEMS_PER_RUN` is 200 and each item costs an HTTP download, a
    parse, a MinIO upload and three DB round trips, so a slow provider puts one
    run past the global `task_time_limit`. That limit is HARD: it kills the
    child process, so no `except Exception` runs, and the run leaves neither
    `resume_sync_failed` nor a cursor write — indistinguishable from a Beat tick
    that never fired.

    What makes the override safe rather than merely bigger is the ORDER below:
    a soft limit strictly under the hard one, so `SoftTimeLimitExceeded` (an
    ordinary `Exception`) reaches the abort handler with time left to write the
    row. Asserting the numbers alone would pass just as happily on a soft limit
    set above the hard one, where the abort handler is never reached at all.
    """
    import backend.app.tasks.wp8 as wp8

    for name in ("sync.pull_dingtalk", "sync.replay_failed", "sync.pull_jds"):
        task = celery_app.tasks[name]
        assert task.soft_time_limit == wp8.SYNC_SOFT_TIME_LIMIT_SECONDS
        assert task.time_limit == wp8.SYNC_HARD_TIME_LIMIT_SECONDS
        # The soft one must fire first, or nothing is ever audited.
        assert task.soft_time_limit < task.time_limit
        # And the whole run must be dead before Beat publishes the next tick,
        # or a slow provider quietly stacks overlapping runs.
        assert task.time_limit < get_settings().DINGTALK_SYNC_INTERVAL_SECONDS
        # The global limit is untouched: it is pinned below
        # `INGESTION_LEASE_SECONDS` so `ingest.parse_and_score` — the task that
        # actually HOLDS a lease — can never be killed while the sweeper still
        # reads its job as live.
        assert task.time_limit > celery_app.conf.task_time_limit

    assert celery_app.conf.task_time_limit == 600


def test_sync_is_not_scheduled_while_disabled() -> None:
    # The kill switch must remove the schedule entry, not merely make the task
    # return early: a registered schedule still wakes a worker every interval.
    # Asserted against the schedule the running process actually has, not
    # against the builder — this is the one that governs a real Beat.
    assert get_settings().DINGTALK_SYNC_ENABLED is False
    assert "wp8-pull-dingtalk" not in celery_app.conf.beat_schedule
    assert "wp8-replay-failed" not in celery_app.conf.beat_schedule
    assert "wp8-sync-jds" not in celery_app.conf.beat_schedule


def test_the_jd_sync_is_scheduled_independently_of_the_resume_pull() -> None:
    """JD sync must be its own entry, not a step inside the resume pull.

    `list_jobs` maps every failure to `SourceUnavailable`, and the jobs endpoint
    is separately permission-granted. Chained in-process, a standing 403 there
    would take resume ingestion down with it — which is exactly what
    `test_a_failing_sync_does_not_block_manual_upload` says must never happen.
    """
    settings = get_settings()
    schedule = _schedule(enabled=True)

    assert schedule["wp8-sync-jds"]["task"] == "sync.pull_jds"
    # Reuses the pull's interval rather than adding a Settings field: JD
    # metadata changes rarely and the extra knob is not worth the surface.
    assert schedule["wp8-sync-jds"]["schedule"] == float(
        settings.DINGTALK_SYNC_INTERVAL_SECONDS
    )
    # Two entries, two dispatches: neither can be the other's failure mode.
    assert schedule["wp8-pull-dingtalk"]["task"] == "sync.pull_dingtalk"


def test_the_switch_schedules_both_sync_tasks_when_it_is_on() -> None:
    """The positive direction — the half the switch exists for.

    Only the disabled branch is reachable from this suite's environment, so
    without this the whole kill-switch block could be deleted and every test
    would still pass: a safety switch with no evidence it ever switches
    anything on.
    """
    settings = get_settings()
    schedule = _schedule(enabled=True)

    assert schedule["wp8-pull-dingtalk"]["task"] == "sync.pull_dingtalk"
    assert schedule["wp8-replay-failed"]["task"] == "sync.replay_failed"
    assert schedule["wp8-pull-dingtalk"]["schedule"] == float(
        settings.DINGTALK_SYNC_INTERVAL_SECONDS
    )
    assert schedule["wp8-replay-failed"]["schedule"] == float(
        settings.SYNC_REPLAY_INTERVAL_SECONDS
    )
    # Adding the entries must not cost the ones already there.
    assert schedule["ingestion-sweep"]["task"] == "ingest.sweep"
    assert schedule["wp7-reconcile-budgets"]["task"] == "wp7.reconcile_budgets"


def test_the_switch_schedules_neither_sync_task_when_it_is_off() -> None:
    schedule = _schedule(enabled=False)

    assert "wp8-pull-dingtalk" not in schedule
    assert "wp8-replay-failed" not in schedule
    assert "wp8-sync-jds" not in schedule
    assert {entry["task"] for entry in schedule.values()}.isdisjoint(
        {"sync.pull_dingtalk", "sync.replay_failed", "sync.pull_jds"}
    )
    assert "ingestion-sweep" in schedule


def test_no_scheduled_task_names_a_wp8_task_while_disabled() -> None:
    """Absent by key is not enough — assert on what would actually be dispatched.

    A future edit could reintroduce the entry under a different key and the
    key-based assertion above would still pass.
    """
    scheduled = {entry["task"] for entry in celery_app.conf.beat_schedule.values()}

    assert "sync.pull_dingtalk" not in scheduled
    assert "sync.replay_failed" not in scheduled
    assert "sync.pull_jds" not in scheduled


def test_the_wp7_schedules_survive() -> None:
    schedule = celery_app.conf.beat_schedule

    assert schedule["ingestion-sweep"]["task"] == "ingest.sweep"
    assert schedule["wp7-reconcile-budgets"]["task"] == "wp7.reconcile_budgets"


def test_the_wp8_task_module_is_included_for_the_worker() -> None:
    # Beat dispatches by name; a worker that never imports the module has no
    # task registered to receive it.
    assert "backend.app.tasks.wp8" in celery_app.conf.include
