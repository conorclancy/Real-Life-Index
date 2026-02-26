"""
Refresh service — orchestrates all data collectors and writes results to the DB.

This is the main entry point called by:
  - The APScheduler jobs (on a schedule)
  - The POST /admin/refresh endpoint (manual trigger)
  - The application startup lifespan (initial data load)
"""

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.bls_client import BLSClient
from app.collectors.eia_client import EIAClient
from app.collectors.static_prices import get_static_price
from app.config import settings
from app.database import AsyncSessionLocal
from app.models import Good, PriceSnapshot

logger = logging.getLogger(__name__)

# Module-level client instances (created once, reused across scheduler runs)
_bls_client = BLSClient(api_key=settings.BLS_API_KEY)
_eia_client = EIAClient(api_key=settings.EIA_API_KEY)


async def run_all_collectors() -> dict[str, str]:
    """
    Run all data collectors and persist the results to the database.

    Returns a status dict summarising what was updated, e.g.:
        {"eggs": "ok", "gasoline": "ok", "mcdonalds": "ok", ...}
    """
    logger.info("Starting data refresh at %s", datetime.utcnow().isoformat())
    status: dict[str, str] = {}

    async with AsyncSessionLocal() as db:
        # --- 1. BLS goods (7 items in a single API call) ---
        bls_goods = await _get_goods_by_source(db, "bls")
        if bls_goods:
            series_ids = [g.source_id for g in bls_goods if g.source_id]
            bls_results = await _bls_client.get_latest_prices(series_ids)

            for good in bls_goods:
                result = bls_results.get(good.source_id or "")
                if result is None:
                    logger.warning("No BLS data returned for %s (%s)", good.slug, good.source_id)
                    status[good.slug] = "no_data"
                    continue

                price, period_label = result
                await _upsert_snapshot(db, good.id, price, period_label)
                status[good.slug] = "ok"
                logger.info("  BLS: %s = $%.2f (%s)", good.slug, price, period_label)
        else:
            logger.warning("No BLS goods found in database — was seed_goods() run?")

        # --- 2. EIA gasoline ---
        gasoline = await _get_good_by_slug(db, "gasoline")
        if gasoline:
            result = await _eia_client.get_latest_gasoline_price()
            if result is None:
                logger.warning("No EIA data returned for gasoline")
                status["gasoline"] = "no_data"
            else:
                price, period_label = result
                await _upsert_snapshot(db, gasoline.id, price, period_label)
                status["gasoline"] = "ok"
                logger.info("  EIA: gasoline = $%.3f (%s)", price, period_label)
        else:
            logger.warning("Gasoline good not found in database")
            status["gasoline"] = "not_found"

        # --- 3. Static prices (McDonald's, Netflix) ---
        static_goods = await _get_goods_by_source(db, "static")
        for good in static_goods:
            price = get_static_price(good.slug)
            if price is None:
                logger.warning("No static price defined for %s", good.slug)
                status[good.slug] = "no_data"
                continue

            # Static goods use a period label based on the current year-month
            # so they get a new snapshot each month (reflecting that we verified
            # the price is still current)
            period_label = datetime.utcnow().strftime("%Y-%m")
            await _upsert_snapshot(db, good.id, price, period_label)
            status[good.slug] = "ok"
            logger.info("  Static: %s = $%.2f", good.slug, price)

        await db.commit()

    logger.info("Data refresh complete. Status: %s", status)
    return status


async def seed_goods(db: AsyncSession) -> None:
    """
    Populate the goods table with the 10 tracked items if it is empty.
    Safe to call on every startup — does nothing if goods already exist.
    """
    from app.models import GOODS_SEED  # noqa: PLC0415

    existing = await db.execute(select(Good))
    if existing.scalars().first() is not None:
        return  # Already seeded

    logger.info("Seeding goods catalogue...")
    for item in GOODS_SEED:
        db.add(Good(**item))
    await db.commit()
    logger.info("Seeded %d goods.", len(GOODS_SEED))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _get_goods_by_source(db: AsyncSession, source: str) -> list[Good]:
    """Return all Good rows with the given source string."""
    result = await db.execute(select(Good).where(Good.source == source))
    return list(result.scalars().all())


async def _get_good_by_slug(db: AsyncSession, slug: str) -> Good | None:
    """Return the Good row for the given slug, or None."""
    result = await db.execute(select(Good).where(Good.slug == slug))
    return result.scalars().first()


async def _upsert_snapshot(
    db: AsyncSession,
    good_id: int,
    price_usd: float,
    period_label: str,
) -> None:
    """
    Insert a price snapshot, or update the price if one already exists for
    this good + period combination (handles re-runs gracefully).
    """
    stmt = (
        sqlite_insert(PriceSnapshot)
        .values(
            good_id=good_id,
            price_usd=price_usd,
            period_label=period_label,
            collected_at=datetime.utcnow(),
        )
        .on_conflict_do_update(
            index_elements=["good_id", "period_label"],
            set_={
                "price_usd": price_usd,
                "collected_at": datetime.utcnow(),
            },
        )
    )
    await db.execute(stmt)
