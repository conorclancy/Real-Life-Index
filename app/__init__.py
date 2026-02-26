"""
FastAPI application factory.

This module creates the app instance, configures middleware, mounts static files
and templates, wires up the router, and manages the application lifespan
(database init, seeding, and the APScheduler background scheduler).
"""

import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.scheduler.jobs import schedule_jobs

# Configure logging for the whole application
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Jinja2 template engine — routes import this to render HTML responses
templates = Jinja2Templates(directory="app/templates")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan handler — runs startup logic before yield,
    and shutdown logic after yield.

    Startup sequence:
      1. Create DB tables (idempotent — safe to run every time)
      2. Seed the goods catalogue (skipped if already seeded)
      3. Run an initial data fetch so the dashboard isn't empty on first launch
      4. Start the APScheduler background scheduler
    """
    # --- Startup ---
    from app.database import AsyncSessionLocal, init_db  # noqa: PLC0415
    from app.services.refresh_service import run_all_collectors, seed_goods  # noqa: PLC0415

    logger.info("Starting up Real Life Index...")

    # 1. Create database tables
    await init_db()
    logger.info("Database tables ready.")

    # 2. Seed the goods catalogue
    async with AsyncSessionLocal() as db:
        await seed_goods(db)

    # 3. Initial data fetch (runs in the background so startup is fast)
    #    Comment this out if you want to trigger manually via /admin/refresh
    logger.info("Running initial data fetch...")
    await run_all_collectors()

    # 4. Start the scheduler
    scheduler = AsyncIOScheduler()
    schedule_jobs(scheduler)
    scheduler.start()
    logger.info("Scheduler started.")

    yield  # App runs here — handling requests

    # --- Shutdown ---
    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped. Goodbye!")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Real Life Index",
        description="US Everyday Price Index — tracking the real cost of everyday goods.",
        version="0.1.0",
        lifespan=lifespan,
        # Disable the default /docs and /redoc in production if desired
    )

    # Serve static files (CSS, JS) from the /static directory
    app.mount("/static", StaticFiles(directory="static"), name="static")

    # Register all route handlers
    from app.api.routes import router  # noqa: PLC0415
    app.include_router(router)

    return app


# The application instance — imported by main.py
app = create_app()
