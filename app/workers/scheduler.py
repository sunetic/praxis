import asyncio

from app.core.config import get_settings
from app.services.scheduler.worker import SchedulerWorker


async def run_scheduler_worker() -> None:
    settings = get_settings()
    worker = SchedulerWorker(
        refresh_interval_seconds=settings.scheduler_refresh_interval_seconds,
        job_coalesce=settings.scheduler_job_coalesce,
        job_misfire_grace_seconds=settings.scheduler_job_misfire_grace_seconds,
        job_max_instances=settings.scheduler_job_max_instances,
    )
    await worker.start()
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await worker.shutdown()


def main() -> None:
    asyncio.run(run_scheduler_worker())


if __name__ == "__main__":
    main()
