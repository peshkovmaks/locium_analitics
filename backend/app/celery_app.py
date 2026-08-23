from celery import Celery
from celery.schedules import crontab
from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "marketplace_tasks",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.sync"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Europe/Moscow",
    enable_utc=True,
    broker_connection_retry_on_startup=True,
    beat_schedule={
        "sync-every-4-hours": {
            "task": "app.tasks.sync.sync_all_shops_task",
            "schedule": 4 * 60 * 60,  # 4 hours
        },
        "daily-report-21-00": {
            "task": "app.tasks.sync.send_daily_report_task",
            "schedule": crontab(hour=21, minute=0),
        },
        "morning-report-9-00": {
            "task": "app.tasks.sync.send_morning_report_task",
            "schedule": crontab(hour=9, minute=0),
        },
        "check-alerts-every-hour": {
            "task": "app.tasks.sync.check_alerts_task",
            "schedule": 60 * 60,  # 1 hour
        },
    },
)
