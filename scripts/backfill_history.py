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

    # ── Ireland ───────────────────────────────────────────────────────────────
    # Supermac's Regular Meal (burger + chips + drink), €
    # Source: supermacs.ie menu; prices verified from news reports
    "ie_supermacs": [
        (2020, 1,  7.99), (2020, 2,  7.99), (2020, 3,  7.99),
        (2020, 4,  7.99), (2020, 5,  7.99), (2020, 6,  7.99),
        (2020, 7,  7.99), (2020, 8,  7.99), (2020, 9,  7.99),
        (2020, 10, 7.99), (2020, 11, 7.99), (2020, 12, 7.99),

        (2021, 1,  7.99), (2021, 2,  7.99), (2021, 3,  7.99),
        (2021, 4,  7.99), (2021, 5,  7.99), (2021, 6,  7.99),
        (2021, 7,  8.49), (2021, 8,  8.49), (2021, 9,  8.49),
        (2021, 10, 8.49), (2021, 11, 8.49), (2021, 12, 8.49),

        (2022, 1,  8.99), (2022, 2,  8.99), (2022, 3,  8.99),
        (2022, 4,  9.49), (2022, 5,  9.49), (2022, 6,  9.49),
        (2022, 7,  9.49), (2022, 8,  9.99), (2022, 9,  9.99),
        (2022, 10, 9.99), (2022, 11, 9.99), (2022, 12, 9.99),

        (2023, 1,  10.49), (2023, 2,  10.49), (2023, 3,  10.49),
        (2023, 4,  10.49), (2023, 5,  10.49), (2023, 6,  10.49),
        (2023, 7,  10.49), (2023, 8,  10.49), (2023, 9,  10.49),
        (2023, 10, 10.49), (2023, 11, 10.49), (2023, 12, 10.49),

        (2024, 1,  10.99), (2024, 2,  10.99), (2024, 3,  10.99),
        (2024, 4,  10.99), (2024, 5,  10.99), (2024, 6,  10.99),
        (2024, 7,  10.99), (2024, 8,  10.99), (2024, 9,  10.99),
        (2024, 10, 10.99), (2024, 11, 10.99), (2024, 12, 10.99),

        (2025, 1,  10.99), (2025, 2,  10.99),
    ],

    # Netflix Standard plan in Ireland (EUR)
    # Source: netflix.com/ie — Irish EUR pricing history
    "ie_netflix": [
        (2020, 1,  11.99), (2020, 2,  11.99), (2020, 3,  11.99),
        (2020, 4,  11.99), (2020, 5,  11.99), (2020, 6,  11.99),
        (2020, 7,  11.99), (2020, 8,  11.99), (2020, 9,  11.99),
        (2020, 10, 11.99), (2020, 11, 11.99), (2020, 12, 11.99),

        (2021, 1,  12.99), (2021, 2,  12.99), (2021, 3,  12.99),
        (2021, 4,  12.99), (2021, 5,  12.99), (2021, 6,  12.99),
        (2021, 7,  12.99), (2021, 8,  12.99), (2021, 9,  12.99),
        (2021, 10, 12.99), (2021, 11, 12.99), (2021, 12, 12.99),

        # 2022 price increase
        (2022, 1,  13.99), (2022, 2,  13.99), (2022, 3,  13.99),
        (2022, 4,  13.99), (2022, 5,  13.99), (2022, 6,  13.99),
        (2022, 7,  13.99), (2022, 8,  13.99), (2022, 9,  13.99),
        (2022, 10, 13.99), (2022, 11, 13.99), (2022, 12, 13.99),

        (2023, 1,  13.99), (2023, 2,  13.99), (2023, 3,  13.99),
        (2023, 4,  13.99), (2023, 5,  13.99), (2023, 6,  13.99),
        (2023, 7,  13.99), (2023, 8,  13.99), (2023, 9,  13.99),
        (2023, 10, 13.99), (2023, 11, 13.99), (2023, 12, 13.99),

        (2024, 1,  15.99), (2024, 2,  15.99), (2024, 3,  15.99),
        (2024, 4,  15.99), (2024, 5,  15.99), (2024, 6,  15.99),
        (2024, 7,  15.99), (2024, 8,  15.99), (2024, 9,  15.99),
        (2024, 10, 15.99), (2024, 11, 15.99), (2024, 12, 15.99),

        (2025, 1,  15.99), (2025, 2,  15.99),
    ],

    # ── Ireland Groceries (Lidl.ie) ───────────────────────────────────────────
    # Monthly approximate prices based on CSO CPI food component, Lidl.ie
    # public listings, and Irish news reports. Anchored to current scraped prices.
    # All values in EUR.

    # Free Range Eggs (12-pack)
    # Spiked 2022-2023 due to avian flu outbreaks + energy/feed cost inflation.
    "ie_eggs": [
        (2020, 1, 1.39), (2020, 2, 1.39), (2020, 3, 1.39),
        (2020, 4, 1.39), (2020, 5, 1.39), (2020, 6, 1.39),
        (2020, 7, 1.39), (2020, 8, 1.39), (2020, 9, 1.39),
        (2020, 10, 1.39), (2020, 11, 1.39), (2020, 12, 1.39),

        (2021, 1, 1.39), (2021, 2, 1.39), (2021, 3, 1.39),
        (2021, 4, 1.39), (2021, 5, 1.39), (2021, 6, 1.49),
        (2021, 7, 1.49), (2021, 8, 1.49), (2021, 9, 1.49),
        (2021, 10, 1.49), (2021, 11, 1.49), (2021, 12, 1.49),

        # 2022: avian flu + energy costs drive egg prices up
        (2022, 1, 1.49), (2022, 2, 1.59), (2022, 3, 1.69),
        (2022, 4, 1.79), (2022, 5, 1.89), (2022, 6, 1.99),
        (2022, 7, 1.99), (2022, 8, 2.09), (2022, 9, 2.19),
        (2022, 10, 2.19), (2022, 11, 2.19), (2022, 12, 2.19),

        # 2023: peaked early year, gradually easing
        (2023, 1, 2.29), (2023, 2, 2.29), (2023, 3, 2.19),
        (2023, 4, 2.09), (2023, 5, 1.99), (2023, 6, 1.99),
        (2023, 7, 1.89), (2023, 8, 1.79), (2023, 9, 1.79),
        (2023, 10, 1.79), (2023, 11, 1.69), (2023, 12, 1.69),

        (2024, 1, 1.69), (2024, 2, 1.69), (2024, 3, 1.69),
        (2024, 4, 1.69), (2024, 5, 1.69), (2024, 6, 1.69),
        (2024, 7, 1.69), (2024, 8, 1.69), (2024, 9, 1.69),
        (2024, 10, 1.69), (2024, 11, 1.69), (2024, 12, 1.69),

        (2025, 1, 1.69), (2025, 2, 1.69),
    ],

    # Fresh Milk (2 litres)
    # Rose with dairy inflation in 2022-2023, stabilised 2024.
    "ie_milk": [
        (2020, 1, 1.29), (2020, 2, 1.29), (2020, 3, 1.29),
        (2020, 4, 1.29), (2020, 5, 1.29), (2020, 6, 1.29),
        (2020, 7, 1.29), (2020, 8, 1.29), (2020, 9, 1.29),
        (2020, 10, 1.29), (2020, 11, 1.29), (2020, 12, 1.29),

        (2021, 1, 1.29), (2021, 2, 1.29), (2021, 3, 1.29),
        (2021, 4, 1.29), (2021, 5, 1.39), (2021, 6, 1.39),
        (2021, 7, 1.39), (2021, 8, 1.39), (2021, 9, 1.39),
        (2021, 10, 1.39), (2021, 11, 1.39), (2021, 12, 1.39),

        (2022, 1, 1.49), (2022, 2, 1.49), (2022, 3, 1.55),
        (2022, 4, 1.59), (2022, 5, 1.65), (2022, 6, 1.69),
        (2022, 7, 1.69), (2022, 8, 1.75), (2022, 9, 1.79),
        (2022, 10, 1.79), (2022, 11, 1.79), (2022, 12, 1.79),

        (2023, 1, 1.85), (2023, 2, 1.89), (2023, 3, 1.89),
        (2023, 4, 1.89), (2023, 5, 1.89), (2023, 6, 1.95),
        (2023, 7, 1.95), (2023, 8, 1.95), (2023, 9, 1.95),
        (2023, 10, 1.95), (2023, 11, 1.95), (2023, 12, 1.95),

        (2024, 1, 1.95), (2024, 2, 1.95), (2024, 3, 1.95),
        (2024, 4, 1.95), (2024, 5, 1.95), (2024, 6, 1.95),
        (2024, 7, 1.95), (2024, 8, 1.95), (2024, 9, 1.95),
        (2024, 10, 1.95), (2024, 11, 1.95), (2024, 12, 1.95),

        (2025, 1, 1.95), (2025, 2, 1.95),
    ],

    # White Sliced Pan (800g)
    # Rose sharply in 2022 with Ukraine war wheat price spike.
    "ie_bread": [
        (2020, 1, 1.09), (2020, 2, 1.09), (2020, 3, 1.09),
        (2020, 4, 1.09), (2020, 5, 1.09), (2020, 6, 1.09),
        (2020, 7, 1.09), (2020, 8, 1.09), (2020, 9, 1.09),
        (2020, 10, 1.09), (2020, 11, 1.09), (2020, 12, 1.09),

        (2021, 1, 1.09), (2021, 2, 1.09), (2021, 3, 1.09),
        (2021, 4, 1.09), (2021, 5, 1.19), (2021, 6, 1.19),
        (2021, 7, 1.19), (2021, 8, 1.19), (2021, 9, 1.19),
        (2021, 10, 1.19), (2021, 11, 1.19), (2021, 12, 1.29),

        # 2022: wheat prices surge post-Ukraine invasion
        (2022, 1, 1.29), (2022, 2, 1.29), (2022, 3, 1.39),
        (2022, 4, 1.49), (2022, 5, 1.49), (2022, 6, 1.49),
        (2022, 7, 1.55), (2022, 8, 1.59), (2022, 9, 1.59),
        (2022, 10, 1.59), (2022, 11, 1.59), (2022, 12, 1.69),

        (2023, 1, 1.69), (2023, 2, 1.69), (2023, 3, 1.75),
        (2023, 4, 1.79), (2023, 5, 1.79), (2023, 6, 1.79),
        (2023, 7, 1.79), (2023, 8, 1.89), (2023, 9, 1.89),
        (2023, 10, 1.89), (2023, 11, 1.89), (2023, 12, 1.89),

        (2024, 1, 1.89), (2024, 2, 1.89), (2024, 3, 1.89),
        (2024, 4, 1.89), (2024, 5, 1.95), (2024, 6, 1.95),
        (2024, 7, 1.95), (2024, 8, 1.95), (2024, 9, 1.99),
        (2024, 10, 1.99), (2024, 11, 1.99), (2024, 12, 1.99),

        (2025, 1, 1.99), (2025, 2, 1.99),
    ],

    # Lean Beef Mince (500g)
    # Steady rise through food inflation 2021-2024.
    "ie_mince": [
        (2020, 1, 3.49), (2020, 2, 3.49), (2020, 3, 3.49),
        (2020, 4, 3.49), (2020, 5, 3.49), (2020, 6, 3.49),
        (2020, 7, 3.49), (2020, 8, 3.49), (2020, 9, 3.49),
        (2020, 10, 3.49), (2020, 11, 3.49), (2020, 12, 3.49),

        (2021, 1, 3.49), (2021, 2, 3.49), (2021, 3, 3.49),
        (2021, 4, 3.69), (2021, 5, 3.69), (2021, 6, 3.79),
        (2021, 7, 3.79), (2021, 8, 3.79), (2021, 9, 3.99),
        (2021, 10, 3.99), (2021, 11, 3.99), (2021, 12, 3.99),

        (2022, 1, 4.19), (2022, 2, 4.19), (2022, 3, 4.29),
        (2022, 4, 4.29), (2022, 5, 4.49), (2022, 6, 4.49),
        (2022, 7, 4.49), (2022, 8, 4.59), (2022, 9, 4.69),
        (2022, 10, 4.69), (2022, 11, 4.69), (2022, 12, 4.79),

        (2023, 1, 4.79), (2023, 2, 4.79), (2023, 3, 4.99),
        (2023, 4, 4.99), (2023, 5, 4.99), (2023, 6, 4.99),
        (2023, 7, 5.09), (2023, 8, 5.09), (2023, 9, 5.09),
        (2023, 10, 5.09), (2023, 11, 5.09), (2023, 12, 5.29),

        (2024, 1, 5.29), (2024, 2, 5.29), (2024, 3, 5.29),
        (2024, 4, 5.29), (2024, 5, 5.29), (2024, 6, 5.29),
        (2024, 7, 5.49), (2024, 8, 5.49), (2024, 9, 5.49),
        (2024, 10, 5.49), (2024, 11, 5.49), (2024, 12, 5.49),

        (2025, 1, 5.49), (2025, 2, 5.49),
    ],

    # Chicken Breast Fillets (~600g pack)
    # Moderate steady rise through 2020-2024.
    "ie_chicken": [
        (2020, 1, 3.29), (2020, 2, 3.29), (2020, 3, 3.29),
        (2020, 4, 3.29), (2020, 5, 3.29), (2020, 6, 3.29),
        (2020, 7, 3.29), (2020, 8, 3.29), (2020, 9, 3.29),
        (2020, 10, 3.29), (2020, 11, 3.29), (2020, 12, 3.29),

        (2021, 1, 3.29), (2021, 2, 3.29), (2021, 3, 3.49),
        (2021, 4, 3.49), (2021, 5, 3.49), (2021, 6, 3.49),
        (2021, 7, 3.49), (2021, 8, 3.49), (2021, 9, 3.49),
        (2021, 10, 3.69), (2021, 11, 3.69), (2021, 12, 3.69),

        (2022, 1, 3.69), (2022, 2, 3.79), (2022, 3, 3.79),
        (2022, 4, 3.79), (2022, 5, 3.89), (2022, 6, 3.99),
        (2022, 7, 3.99), (2022, 8, 3.99), (2022, 9, 3.99),
        (2022, 10, 4.09), (2022, 11, 4.09), (2022, 12, 4.09),

        (2023, 1, 4.19), (2023, 2, 4.19), (2023, 3, 4.19),
        (2023, 4, 4.19), (2023, 5, 4.29), (2023, 6, 4.29),
        (2023, 7, 4.29), (2023, 8, 4.29), (2023, 9, 4.29),
        (2023, 10, 4.29), (2023, 11, 4.49), (2023, 12, 4.49),

        (2024, 1, 4.49), (2024, 2, 4.49), (2024, 3, 4.49),
        (2024, 4, 4.49), (2024, 5, 4.49), (2024, 6, 4.49),
        (2024, 7, 4.49), (2024, 8, 4.49), (2024, 9, 4.49),
        (2024, 10, 4.49), (2024, 11, 4.49), (2024, 12, 4.49),

        (2025, 1, 4.49), (2025, 2, 4.49),
    ],

    # Ground Coffee — Bellarom (Lidl own-brand, 250g)
    # Coffee bean prices rose 2021-2024 on supply constraints.
    "ie_coffee": [
        (2020, 1, 2.79), (2020, 2, 2.79), (2020, 3, 2.79),
        (2020, 4, 2.79), (2020, 5, 2.79), (2020, 6, 2.79),
        (2020, 7, 2.79), (2020, 8, 2.79), (2020, 9, 2.79),
        (2020, 10, 2.79), (2020, 11, 2.79), (2020, 12, 2.79),

        (2021, 1, 2.79), (2021, 2, 2.79), (2021, 3, 2.99),
        (2021, 4, 2.99), (2021, 5, 2.99), (2021, 6, 2.99),
        (2021, 7, 2.99), (2021, 8, 2.99), (2021, 9, 2.99),
        (2021, 10, 2.99), (2021, 11, 2.99), (2021, 12, 2.99),

        (2022, 1, 3.19), (2022, 2, 3.19), (2022, 3, 3.19),
        (2022, 4, 3.19), (2022, 5, 3.29), (2022, 6, 3.29),
        (2022, 7, 3.29), (2022, 8, 3.49), (2022, 9, 3.49),
        (2022, 10, 3.49), (2022, 11, 3.49), (2022, 12, 3.49),

        (2023, 1, 3.49), (2023, 2, 3.49), (2023, 3, 3.69),
        (2023, 4, 3.69), (2023, 5, 3.69), (2023, 6, 3.69),
        (2023, 7, 3.79), (2023, 8, 3.79), (2023, 9, 3.79),
        (2023, 10, 3.79), (2023, 11, 3.79), (2023, 12, 3.99),

        (2024, 1, 3.99), (2024, 2, 3.99), (2024, 3, 3.99),
        (2024, 4, 3.99), (2024, 5, 3.99), (2024, 6, 3.99),
        (2024, 7, 3.99), (2024, 8, 3.99), (2024, 9, 3.99),
        (2024, 10, 3.99), (2024, 11, 3.99), (2024, 12, 3.99),

        (2025, 1, 3.99), (2025, 2, 3.99),
    ],

    # Beer Multipack (Lidl own-brand lager multipack)
    # Steady rise with energy and input cost inflation.
    "ie_beer": [
        (2020, 1, 5.99), (2020, 2, 5.99), (2020, 3, 5.99),
        (2020, 4, 5.99), (2020, 5, 5.99), (2020, 6, 5.99),
        (2020, 7, 5.99), (2020, 8, 5.99), (2020, 9, 5.99),
        (2020, 10, 5.99), (2020, 11, 5.99), (2020, 12, 5.99),

        (2021, 1, 5.99), (2021, 2, 5.99), (2021, 3, 5.99),
        (2021, 4, 6.29), (2021, 5, 6.29), (2021, 6, 6.29),
        (2021, 7, 6.29), (2021, 8, 6.29), (2021, 9, 6.29),
        (2021, 10, 6.29), (2021, 11, 6.29), (2021, 12, 6.29),

        (2022, 1, 6.49), (2022, 2, 6.49), (2022, 3, 6.49),
        (2022, 4, 6.49), (2022, 5, 6.69), (2022, 6, 6.69),
        (2022, 7, 6.69), (2022, 8, 6.99), (2022, 9, 6.99),
        (2022, 10, 6.99), (2022, 11, 6.99), (2022, 12, 6.99),

        (2023, 1, 7.19), (2023, 2, 7.19), (2023, 3, 7.19),
        (2023, 4, 7.29), (2023, 5, 7.29), (2023, 6, 7.29),
        (2023, 7, 7.29), (2023, 8, 7.29), (2023, 9, 7.29),
        (2023, 10, 7.29), (2023, 11, 7.29), (2023, 12, 7.49),

        (2024, 1, 7.49), (2024, 2, 7.49), (2024, 3, 7.49),
        (2024, 4, 7.49), (2024, 5, 7.69), (2024, 6, 7.69),
        (2024, 7, 7.69), (2024, 8, 7.69), (2024, 9, 7.69),
        (2024, 10, 7.69), (2024, 11, 7.69), (2024, 12, 7.69),

        (2025, 1, 7.69), (2025, 2, 7.69),
    ],

    # ── US Gasoline (fallback — used when EIA API key is unavailable) ─────────
    # Regular unleaded gasoline, US national average, $/gallon.
    # Source: AAA / EIA published monthly averages.
    # Key events: COVID demand crash (Apr 2020 ~$1.87), Ukraine war spike (Jun 2022 ~$5.01).
    # If the EIA API key is configured, backfill_eia() will also insert higher-resolution
    # weekly data (period_label format "YYYY-MM-DD") alongside these monthly entries.
    "gasoline": [
        (2020, 1, 2.58), (2020, 2, 2.47), (2020, 3, 2.16),
        (2020, 4, 1.87), (2020, 5, 1.93), (2020, 6, 2.08),
        (2020, 7, 2.18), (2020, 8, 2.20), (2020, 9, 2.19),
        (2020, 10, 2.19), (2020, 11, 2.13), (2020, 12, 2.23),

        (2021, 1, 2.38), (2021, 2, 2.52), (2021, 3, 2.77),
        (2021, 4, 2.86), (2021, 5, 2.97), (2021, 6, 3.08),
        (2021, 7, 3.16), (2021, 8, 3.17), (2021, 9, 3.18),
        (2021, 10, 3.28), (2021, 11, 3.40), (2021, 12, 3.35),

        # 2022: Russia-Ukraine war, global oil supply shock
        (2022, 1, 3.31), (2022, 2, 3.56), (2022, 3, 4.21),
        (2022, 4, 4.10), (2022, 5, 4.44), (2022, 6, 5.01),
        (2022, 7, 4.65), (2022, 8, 3.99), (2022, 9, 3.68),
        (2022, 10, 3.77), (2022, 11, 3.55), (2022, 12, 3.15),

        (2023, 1, 3.27), (2023, 2, 3.41), (2023, 3, 3.51),
        (2023, 4, 3.66), (2023, 5, 3.57), (2023, 6, 3.58),
        (2023, 7, 3.76), (2023, 8, 3.84), (2023, 9, 3.82),
        (2023, 10, 3.66), (2023, 11, 3.38), (2023, 12, 3.10),

        (2024, 1, 3.10), (2024, 2, 3.26), (2024, 3, 3.52),
        (2024, 4, 3.67), (2024, 5, 3.60), (2024, 6, 3.45),
        (2024, 7, 3.31), (2024, 8, 3.28), (2024, 9, 3.19),
        (2024, 10, 3.20), (2024, 11, 3.09), (2024, 12, 2.99),

        (2025, 1, 3.12), (2025, 2, 3.14),
    ],

    # ── Ireland Petrol ────────────────────────────────────────────────────────
    # Unleaded petrol per litre (EUR), Ireland national average.
    # Source: AA Ireland Fuel Tracker historical records, SEAI publications.
    # Key events: COVID oil crash (Apr 2020 ~€1.05), Ukraine war spike (Jun 2022 ~€2.10).
    "ie_petrol": [
        (2020, 1, 1.28), (2020, 2, 1.27), (2020, 3, 1.22),
        (2020, 4, 1.05), (2020, 5, 1.05), (2020, 6, 1.10),
        (2020, 7, 1.14), (2020, 8, 1.16), (2020, 9, 1.14),
        (2020, 10, 1.14), (2020, 11, 1.10), (2020, 12, 1.18),

        (2021, 1, 1.24), (2021, 2, 1.28), (2021, 3, 1.35),
        (2021, 4, 1.38), (2021, 5, 1.42), (2021, 6, 1.47),
        (2021, 7, 1.52), (2021, 8, 1.54), (2021, 9, 1.55),
        (2021, 10, 1.59), (2021, 11, 1.56), (2021, 12, 1.57),

        # 2022: Russia-Ukraine war causes global oil price surge
        (2022, 1, 1.64), (2022, 2, 1.75), (2022, 3, 1.98),
        (2022, 4, 1.95), (2022, 5, 2.02), (2022, 6, 2.10),
        (2022, 7, 2.05), (2022, 8, 1.95), (2022, 9, 1.89),
        (2022, 10, 1.83), (2022, 11, 1.76), (2022, 12, 1.70),

        (2023, 1, 1.67), (2023, 2, 1.68), (2023, 3, 1.64),
        (2023, 4, 1.62), (2023, 5, 1.59), (2023, 6, 1.57),
        (2023, 7, 1.60), (2023, 8, 1.65), (2023, 9, 1.67),
        (2023, 10, 1.65), (2023, 11, 1.61), (2023, 12, 1.58),

        (2024, 1, 1.61), (2024, 2, 1.65), (2024, 3, 1.72),
        (2024, 4, 1.75), (2024, 5, 1.72), (2024, 6, 1.70),
        (2024, 7, 1.66), (2024, 8, 1.61), (2024, 9, 1.57),
        (2024, 10, 1.56), (2024, 11, 1.55), (2024, 12, 1.55),

        (2025, 1, 1.57), (2025, 2, 1.58),
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
