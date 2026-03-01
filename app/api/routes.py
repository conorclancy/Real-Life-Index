"""
FastAPI route handlers — all HTTP endpoints for the application.

HTML routes:
  GET /              -> Dashboard home (cards + overview chart)
  GET /good/{slug}   -> Per-good detail page

JSON API routes:
  GET /api/prices                      -> Latest price for every good
  GET /api/prices/{slug}/history       -> Full time series for one good
  POST /admin/refresh                  -> Trigger manual data collection
  GET /health                          -> Health check
"""

import logging
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from fastapi.requests import Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import GoodDetailSchema, GoodSummarySchema, RefreshStatusSchema, SnapshotSchema
from app.services import price_service, refresh_service

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# HTML routes
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def dashboard_home(
    request: Request,
    country: str = Query(default="us", pattern="^(us|ie)$"),
    db: AsyncSession = Depends(get_db),
):
    """
    Main dashboard page.
    Renders a grid of price cards and a normalised index overview chart.
    Use ?country=ie to switch to the Ireland basket.
    """
    from app import templates  # noqa: PLC0415

    summaries = await price_service.build_good_summaries(db, country=country)
    overview_chart_json = await price_service.build_overview_chart_json(db, country=country)

    sparklines = {
        s.slug: price_service.build_sparkline_json(s.sparkline)
        for s in summaries
    }

    last_updated = None
    for s in summaries:
        if s.latest_period:
            last_updated = s.latest_period
            break

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "summaries": summaries,
            "overview_chart_json": overview_chart_json,
            "sparklines": sparklines,
            "last_updated": last_updated or "No data yet",
            "country": country,
        },
    )


@router.get("/compare", response_class=HTMLResponse)
async def compare_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Side-by-side comparison of equivalent goods across the US and Ireland baskets.
    """
    from app import templates  # noqa: PLC0415
    from app.services import fx_service  # noqa: PLC0415

    pairs = await price_service.build_comparison_pairs(db)
    fx_rate, fx_date = await fx_service.fetch_eur_per_usd()

    return templates.TemplateResponse(
        request=request,
        name="compare.html",
        context={
            "pairs": pairs,
            "fx_rate": fx_rate,   # EUR per 1 USD  e.g. 0.9234
            "fx_date": fx_date,   # "YYYY-MM-DD" or "fallback"
        },
    )


@router.get("/good/{slug}", response_class=HTMLResponse)
async def good_detail(
    slug: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Detail page for a single good.
    Shows full price history with an interactive chart and key statistics.
    """
    from app import templates  # noqa: PLC0415

    detail = await price_service.build_good_detail(db, slug)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Good '{slug}' not found")

    detail_chart_json = price_service.build_detail_chart_json(detail)

    return templates.TemplateResponse(
        request=request,
        name="detail.html",
        context={
            "detail": detail,
            "detail_chart_json": detail_chart_json,
        },
    )


# ---------------------------------------------------------------------------
# JSON API routes
# ---------------------------------------------------------------------------

@router.get("/api/prices", response_model=list[GoodSummarySchema])
async def api_all_prices(db: AsyncSession = Depends(get_db)):
    """Return the latest price summary for all tracked goods."""
    return await price_service.build_good_summaries(db)


@router.get("/api/prices/{slug}/history", response_model=list[SnapshotSchema])
async def api_price_history(
    slug: str,
    months: int = Query(default=12, ge=1, le=120, description="Number of months of history"),
    db: AsyncSession = Depends(get_db),
):
    """
    Return the price history for a single good.
    Use the ?months= parameter to control how far back to go (1–120 months).
    """
    good = await price_service.get_good_by_slug(db, slug)
    if not good:
        raise HTTPException(status_code=404, detail=f"Good '{slug}' not found")

    snapshots = await price_service.get_snapshots_for_good(db, good.id, months=months)
    return [
        SnapshotSchema(
            period_label=s.period_label or "",
            price_usd=s.price_usd,
            collected_at=s.collected_at,
        )
        for s in snapshots
    ]


@router.post("/admin/refresh", response_model=RefreshStatusSchema, status_code=202)
async def trigger_refresh(
    background_tasks: BackgroundTasks,
    x_admin_token: str = Header(..., description="Admin token from .env ADMIN_TOKEN"),
):
    """
    Manually trigger a full data refresh.
    Runs in the background — returns 202 Accepted immediately.
    Protect this endpoint by setting a strong ADMIN_TOKEN in your .env file.
    """
    from app.config import settings  # noqa: PLC0415

    if x_admin_token != settings.ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid admin token")

    background_tasks.add_task(refresh_service.run_all_collectors)
    logger.info("Manual refresh triggered via /admin/refresh")

    return RefreshStatusSchema(
        message="Data refresh started in background",
        triggered_at=datetime.utcnow(),
    )


@router.get("/health")
async def health_check():
    """Simple health check endpoint — useful for uptime monitoring."""
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}
