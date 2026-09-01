"""Celery 应用实例。"""
from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "kbase",
    broker=settings.RABBITMQ_URL,
    backend=settings.REDIS_URL,
    include=["app.tasks.document_tasks"],
)

celery_app.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)
