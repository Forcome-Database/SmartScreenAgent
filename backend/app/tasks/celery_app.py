from celery import Celery

from backend.app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "smartscreen",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "backend.app.tasks.ingest",
        "backend.app.tasks.sweep",
        "backend.app.tasks.wp7",
        "backend.app.tasks.wp8",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    # Must stay below `INGESTION_LEASE_SECONDS` (see config.py) so a task is
    # never force-killed while its ingestion job's lease is still considered
    # live — otherwise the sweeper could reclaim a job the worker still owns.
    task_time_limit=600,
    worker_max_tasks_per_child=100,
)

celery_app.conf.beat_schedule = {
    "ingestion-sweep": {
        "task": "ingest.sweep",
        "schedule": float(settings.INGESTION_SWEEP_INTERVAL_SECONDS),
    },
    "wp7-reconcile-budgets": {
        "task": "wp7.reconcile_budgets",
        "schedule": 300.0,
    },
    "wp7-sweep-stale-usage": {
        "task": "wp7.sweep_stale_usage",
        "schedule": 60.0,
    },
    "wp7-sweep-cross-checks": {
        "task": "wp7.sweep_cross_checks",
        "schedule": float(settings.CROSS_ENGINE_SWEEP_INTERVAL_SECONDS),
    },
}

# WP8 sync is scheduled only while the kill switch is on, and the switch is off
# by default. The entry is ADDED here rather than registered always and skipped
# inside the task: a registered schedule wakes a worker every interval forever
# to do nothing, and it puts a live DingTalk pull one config typo away from an
# unverified endpoint. Absent means absent.
if settings.DINGTALK_SYNC_ENABLED:
    celery_app.conf.beat_schedule["wp8-pull-dingtalk"] = {
        "task": "sync.pull_dingtalk",
        "schedule": float(settings.DINGTALK_SYNC_INTERVAL_SECONDS),
    }
    celery_app.conf.beat_schedule["wp8-replay-failed"] = {
        "task": "sync.replay_failed",
        "schedule": float(settings.SYNC_REPLAY_INTERVAL_SECONDS),
    }


@celery_app.task(name="smartscreen.ping")
def ping() -> str:
    """烟测任务：worker 起来后可触发以确认链路通。"""
    return "pong"
