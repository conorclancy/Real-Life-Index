"""
Historical data backfill script.

Populates the database with several years of historical price data so the
dashboard charts have something to show immediately, rather than waiting
months for data to accumulate.

Sources:
  - BLS Average Retail Prices API  → 7 goods, ~5 years of monthly data
  - EIA Open Data API              → gasoline, ~5 years of weekly data
  - Hardcoded table                → McDonald's and Netflix (no historical API exists)

Run with:
    uv run python scripts/backfill_history.py

This script is safe to re-run — it uses upsert logic so it won't create
duplicate rows if you run it more than once.
"""

import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

# Make sure the project root is on the path so `app` imports work
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import settings
from app.collectors.bls_client import BLSClient
from app.collectors.eia_client import EIAClient
from app.database import AsyncSessionLocal, init_db
from app.models import Good, PriceSnapshot, GOODS_SEED
from app.services.refresh_service import seed_goods

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# How many years of history to fetch
# ---------------------------------------------------------------------------
YEARS_BACK = 5

# ---------------------------------------------------------------------------
# Approximate historical prices for goods with no API.
#
# These are US national average estimates based on publicly reported data.
# Each entry is (year, month, price_usd).
# Sources: Consumer price tracking sites, news reports, company announcements.
#
# McDonald's: Big Mac Extra Value Meal (medium), US average
# Netflix:    Standard plan (1080p, 2 screens), US pricing
# ---------------------------------------------------------------------------
STATIC_HISTORY: dict[str, list[tuple[int, int, float]]] = {
    "mcdonalds": [
        # (year, month, price)
        # Prices rose significantly post-COVID due to wage and food cost inflation
        (2020, 1,  5.99), (2020, 2,  5.99), (2020, 3,  5.99),
        (2020, 4,  5.99), (2020, 5,  5.99), (2020, 6,  5.99),
        (2020, 7,  6.09), (2020, 8,  6.09), (2020, 9,  6.09),
        (2020, 10, 6.09), (2020, 11, 6.09), (2020, 12, 6.09),

        (2021, 1,  6.19), (2021, 2,  6.19), (2021, 3,  6.19),
        (2021, 4,  6.29), (2021, 5,  6.39), (2021, 6,  6.49),
        (2021, 7,  6.59), (2021, 8,  6.69), (2021, 9,  6.79),
        (2021, 10, 6.89), (2021, 11, 6.99), (2021, 12, 6.99),

        (2022, 1,  7.19), (2022, 2,  7.29), (2022, 3,  7.49),
        (2022, 4,  7.59), (2022, 5,  7.69), (2022, 6,  7.79),
        (2022, 7,  7.89), (2022, 8,  7.99), (2022, 9,  7.99),
        (2022, 10, 8.09), (2022, 11, 8.19), (2022, 12, 8.29),

        (2023, 1,  8.39), (2023, 2,  8.49), (2023, 3,  8.59),
        (2023, 4,  8.69), (2023, 5,  8.79), (2023, 6,  8.89),
        (2023, 7,  8.99), (2023, 8,  9.09), (2023, 9,  9.09),
        (2023, 10, 9.09), (2023, 11, 9.19), (2023, 12, 9.19),

        (2024, 1,  9.19), (2024, 2,  9.19), (2024, 3,  9.29),
        (2024, 4,  9.29), (2024, 5,  9.29), (2024, 6,  9.29),
        (2024, 7,  9.29), (2024, 8,  9.29), (2024, 9,  9.29),
        (2024, 10, 9.29), (2024, 11, 9.29), (2024, 12, 9.29),

        (2025, 1,  9.29), (2025, 2,  9.29),
    ],
    "netflix": [
        # Netflix Standard plan US pricing history
        # Jan 2022: raised from $13.99 → $15.49
        # Oct 2023: removed cheaper plan, Standard held at $15.49
        # Jan 2025: raised to $17.99
        (2020, 1,  13.99), (2020, 2,  13.99), (2020, 3,  13.99),
        (2020, 4,  13.99), (2020, 5,  13.99), (2020, 6,  13.99),
        (2020, 7,  13.99), (2020, 8,  13.99), (2020, 9,  13.99),
        (2020, 10, 13.99), (2020, 11, 13.99), (2020, 12, 13.99),

        (2021, 1,  13.99), (2021, 2,  13.99), (2021, 3,  13.99),
        (2021, 4,  13.99), (2021, 5,  13.99), (2021, 6,  13.99),
        (2021, 7,  13.99), (2021, 8,  13.99), (2021, 9,  13.99),
        (2021, 10, 13.99), (2021, 11, 13.99), (2021, 12, 13.99),

        # Jan 2022 price increase
        (2022, 1,  15.49), (2022, 2,  15.49), (2022, 3,  15.49),
        (2022, 4,  15.49), (2022, 5,  15.49), (2022, 6,  15.49),
        (2022, 7,  15.49), (2022, 8,  15.49), (2022, 9,  15.49),
        (2022, 10, 15.49), (2022, 11, 15.49), (2022, 12, 15.49),

        (2023, 1,  15.49), (2023, 2,  15.49), (2023, 3,  15.49),
        (2023, 4,  15.49), (2023, 5,  15.49), (2023, 6,  15.49),
        (2023, 7,  15.49), (2023, 8,  15.49), (2023, 9,  15.49),
        (2023, 10, 15.49), (2023, 11, 15.49), (2023, 12, 15.49),

        (2024, 1,  15.49), (2024, 2,  15.49), (2024, 3,  15.49),
        (2024, 4,  15.49), (2024, 5,  15.49), (2024, 6,  15.49),
        (2024, 7,  15.49), (2024, 8,  15.49), (2024, 9,  15.49),
        (2024, 10, 15.49), (2024, 11, 15.49), (2024, 12, 15.49),

        # Jan 2025 price increase
        (2025, 1,  17.99), (2025, 2,  17.99),
    ],
}


async def backfill_bls(bls_client: BLSClient) -> None:
    """Fetch BLS historical monthly prices for all 7 BLS goods."""
    logger.info("--- BLS: Fetching %d years of monthly grocery prices ---", YEARS_BACK)

    # Collect all BLS series IDs from the seed catalogue
    bls_series = {
        item["source_id"]: item["slug"]
        for item in GOODS_SEED
        if item["source"] == "bls" and item["source_id"]
    }

    end_year = datetime.now().year
    start_year = end_year - YEARS_BACK

    # BLS API allows up to 20 years and 50 series per call — fetch everything at once
    logger.info(
        "Fetching %d series from %d to %d in one API call...",
        len(bls_series), start_year, end_year,
    )
    all_data = await bls_client.fetch_series(
        series_ids=list(bls_series.keys()),
        start_year=str(start_year),
        end_year=str(end_year),
    )

    if not all_data:
        logger.error("BLS API returned no data. Check your BLS_API_KEY in .env")
        return

    async with AsyncSessionLocal() as db:
        for series_id, slug in bls_series.items():
            data_points = all_data.get(series_id, [])
            if not data_points:
                logger.warning("  No data for %s (%s)", slug, series_id)
                continue

            # Get the Good row from the DB
            result = await db.execute(select(Good).where(Good.slug == slug))
            good = result.scalars().first()
            if not good:
                logger.warning("  Good '%s' not in database — was seed_goods() run?", slug)
                continue

            inserted = 0
            for point in data_points:
                # Skip annual averages (M13) and non-monthly entries
                period = point.get("period", "")
                if not period.startswith("M") or period == "M13":
                    continue

                value_str = point.get("value", "")
                if value_str in ("", "-", "N/A"):
                    continue

                try:
                    price = float(value_str)
                except ValueError:
                    continue

                # Convert BLS "YYYY" + "M12" -> "2024-12"
                month = period.lstrip("M").zfill(2)
                period_label = f"{point['year']}-{month}"

                # collected_at: set to the 1st of the period month for correct ordering
                collected_at = datetime(int(point["year"]), int(month), 1)

                await _upsert(db, good.id, price, period_label, collected_at)
                inserted += 1

            await db.commit()
            logger.info("  %-15s %d months of data inserted/updated", slug, inserted)


async def backfill_eia(eia_client: EIAClient) -> None:
    """Fetch EIA historical weekly gasoline prices."""
    logger.info("--- EIA: Fetching %d years of weekly gasoline prices ---", YEARS_BACK)

    weeks = YEARS_BACK * 52
    history = await eia_client.get_historical_gasoline_prices(weeks=weeks)

    if not history:
        logger.error("EIA API returned no data.")
        return

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Good).where(Good.slug == "gasoline"))
        good = result.scalars().first()
        if not good:
            logger.error("Gasoline good not found in database.")
            return

        inserted = 0
        for period_label, price in history:
            # EIA period is already "YYYY-MM-DD" (week-ending date)
            try:
                collected_at = datetime.strptime(period_label, "%Y-%m-%d")
            except ValueError:
                collected_at = datetime.utcnow()

            await _upsert(db, good.id, price, period_label, collected_at)
            inserted += 1

        await db.commit()
        logger.info("  gasoline: %d weeks of data inserted/updated", inserted)


async def backfill_static() -> None:
    """Insert hardcoded historical prices for McDonald's and Netflix."""
    logger.info("--- Static: Inserting historical McDonald's and Netflix prices ---")

    async with AsyncSessionLocal() as db:
        for slug, entries in STATIC_HISTORY.items():
            result = await db.execute(select(Good).where(Good.slug == slug))
            good = result.scalars().first()
            if not good:
                logger.warning("  Good '%s' not in database.", slug)
                continue

            inserted = 0
            for year, month, price in entries:
                period_label = f"{year}-{month:02d}"
                collected_at = datetime(year, month, 1)
                await _upsert(db, good.id, price, period_label, collected_at)
                inserted += 1

            await db.commit()
            logger.info("  %-15s %d months of data inserted/updated", slug, inserted)


async def _upsert(db, good_id: int, price: float, period_label: str, collected_at: datetime) -> None:
    """Insert a snapshot or update it if the same good+period already exists."""
    stmt = (
        sqlite_insert(PriceSnapshot)
        .values(
            good_id=good_id,
            price_usd=price,
            period_label=period_label,
            collected_at=collected_at,
        )
        .on_conflict_do_update(
            index_elements=["good_id", "period_label"],
            set_={
                "price_usd": price,
                "collected_at": collected_at,
            },
        )
    )
    await db.execute(stmt)


async def main() -> None:
    logger.info("========================================")
    logger.info("Real Life Index — Historical Backfill")
    logger.info("========================================")

    if not settings.BLS_API_KEY:
        logger.warning(
            "BLS_API_KEY is not set in .env — BLS data fetch may be rate-limited "
            "(25 requests/day without a key). EIA and static data will still work."
        )

    # 1. Ensure database and tables exist
    logger.info("Initialising database...")
    await init_db()

    # 2. Seed goods catalogue if empty
    async with AsyncSessionLocal() as db:
        await seed_goods(db)

    # 3. Backfill each source
    bls_client = BLSClient(api_key=settings.BLS_API_KEY)
    eia_client = EIAClient(api_key=settings.EIA_API_KEY)

    await backfill_bls(bls_client)
    await backfill_eia(eia_client)
    await backfill_static()

    logger.info("========================================")
    logger.info("Backfill complete! Start the app with:")
    logger.info("  uv run python main.py")
    logger.info("========================================")


if __name__ == "__main__":
    asyncio.run(main())
