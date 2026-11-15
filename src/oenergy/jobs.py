from __future__ import annotations

from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from oenergy.config import Settings, get_settings
from oenergy.services.sync import run_job

_scheduler: BackgroundScheduler | None = None


def _job(name: str) -> None:
    try:
        run = run_job(name)
        logger.info("Job {} finished with status {} ({} items)", name, run.status, run.items)
    except Exception:
        logger.exception("Job {} crashed", name)


def start_scheduler(settings: Settings | None = None) -> BackgroundScheduler | None:
    """Start the background sync scheduler."""
    global _scheduler
    settings = settings or get_settings()
    if not settings.scheduler_enabled:
        logger.info("Scheduler disabled")
        return None
    if _scheduler is not None:
        return _scheduler

    scheduler = BackgroundScheduler(timezone=settings.timezone)
    scheduler.add_job(
        _job,
        IntervalTrigger(minutes=settings.poll_interval_minutes),
        args=["prices"],
        id="prices",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _job,
        CronTrigger(minute="7"),
        args=["readings"],
        id="readings",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    # The utility delivers yesterday's values around 08:00 - finalise the day afterwards.
    scheduler.add_job(
        _job,
        CronTrigger(hour=8, minute=17),
        args=["finalize"],
        id="finalize",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _job,
        CronTrigger(hour=6, minute=12),
        args=["bills"],
        id="bills",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _job,
        IntervalTrigger(hours=12),
        args=["account"],
        id="account",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "Scheduler started (prices every {} min, readings hourly, finalize daily 08:17, bills daily 06:12)",
        settings.poll_interval_minutes,
    )
    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def scheduled_jobs() -> list[dict]:
    if _scheduler is None:
        return []
    jobs = []
    for job in _scheduler.get_jobs():
        next_run = getattr(job, "next_run_time", None)
        jobs.append(
            {
                "id": job.id,
                "next_run": next_run.replace(tzinfo=None) if next_run else None,
            }
        )
    return jobs


def now() -> datetime:
    return datetime.utcnow()
