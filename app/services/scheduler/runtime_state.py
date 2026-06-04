from __future__ import annotations

from app.services.scheduler.worker import SchedulerWorker

_scheduler_worker: SchedulerWorker | None = None


def set_scheduler_worker(worker: SchedulerWorker | None) -> None:
    global _scheduler_worker
    _scheduler_worker = worker


def get_scheduler_worker() -> SchedulerWorker | None:
    return _scheduler_worker
