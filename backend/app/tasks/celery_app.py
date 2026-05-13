from celery import Celery

from backend.app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "smartscreen",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["backend.app.tasks.ingest"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=600,
    worker_max_tasks_per_child=100,
)


@celery_app.task(name="smartscreen.ping")
def ping() -> str:
    """烟测任务：worker 起来后可触发以确认链路通。"""
    return "pong"
