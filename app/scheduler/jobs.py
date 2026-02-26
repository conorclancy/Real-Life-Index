"""
APScheduler job definitions for automatic price data collection.

Jobs are registered with the scheduler in the FastAPI lifespan handler
(app/__init__.py). Two schedules run:
  - Monthly: BLS data (releases mid-month for the prior month)
  - Weekly:  EIA gasoline data (releases every Monday afternoon)
"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)


def schedule_jobs(scheduler: AsyncIOScheduler) -> None:
    """
    Register all recurring data collection jobs with the scheduler.
    Called once during app startup — the scheduler handles the rest.
    """
    # Import here to avoid circular imports at module load time
    from app.services.refresh_service import run_all_collectors  # noqa: PLC0415

    # --- Monthly refresh ---
    # BLS publishes prior-month retail price data around the 15th of each month.
    # Running on the 16th gives a small buffer for publication delays.
    scheduler.add_job(
        run_all_collectors,
        trigger="cron",
        day=16,
        hour=10,
        minute=0,
        id="monthly_full_refresh",
        replace_existing=True,
        misfire_grace_time=3600,  # Run within 1 hour of the scheduled time if missed
    )
    logger.info("Scheduled monthly refresh: 16th of each month at 10:00 UTC")

    # --- Weekly gasoline refresh ---
    # EIA releases weekly gas prices every Monday afternoon ET (~4pm).
    # Running Monday at 20:00 UTC (4pm ET) catches the fresh data.
    scheduler.add_job(
        run_all_collectors,
        trigger="cron",
        day_of_week="mon",
        hour=20,
        minute=0,
        id="weekly_gasoline_refresh",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    logger.info("Scheduled weekly refresh: every Monday at 20:00 UTC")
