"""
CSO (Central Statistics Office Ireland) price backfill.

Uses CSO StatBank table CPM12 "National Average Price" which contains
actual monthly consumer prices (EUR) for individual grocery items and
fuel prices in Ireland, going back to December 2011.

For goods with a direct CPM12 match, the official national average price is
used as-is.  For goods where Lidl's pack size / product differs from the
CPM12 item, the CPM12 series is used as a trend line, scaled to the current
Lidl price from the database.

CPM12 item mappings
-------------------
  ie_bread  : '10040' Bread, white sliced pan, large (800g)  → direct
  ie_milk   : '10630' Full fat milk per 2 litre              → direct
  ie_petrol : '30190' Petrol - unleaded per litre            → direct
  ie_eggs   : '10730' Large eggs per half dozen              → ×2 for per dozen
  ie_chicken: '10370' Uncooked chicken medium size 1.6kg     → scaled to Lidl fillets
  ie_mince  : '10310' Sliced / diced beef pieces per kg      → scaled to Lidl mince
  ie_beer   : '11760' Lager - take home (50cl can)           → scaled to Lidl multipack

  ie_coffee is not in CPM12 — uses CPM01 "Food and Non-Alcoholic Beverages"
  CPI index as a trend, scaled to the current Lidl Bellarom price.

Run with:
    uv run python scripts/backfill_cso.py

Safe to re-run — uses upsert logic so no duplicates are created.
Overwrites the approximate static estimates previously stored for IE goods.
"""

import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.database import AsyncSessionLocal, init_db
from app.models import Good, PriceSnapshot
from app.services.refresh_service import seed_goods

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CSO API endpoints
# ---------------------------------------------------------------------------
CSO_CPM12_URL = (
    "https://ws.cso.ie/public/api.restful/PxStat.Data.Cube_API.ReadDataset"
    "/CPM12/JSON-stat/2.0/en/"
)
CSO_CPM01_URL = (
    "https://ws.cso.ie/public/api.restful/PxStat.Data.Cube_API.ReadDataset"
    "/CPM01/JSON-stat/2.0/en/"
)

# ---------------------------------------------------------------------------
# CPM12 item code → (slug, mode, multiplier)
#
# mode:
#   "direct" — use CPM12 price as-is (only for petrol: national avg = pump price)
#   "scaled" — use CPM12 as a trend line scaled to the current Lidl price in
#              the DB.  Necessary because Lidl prices are typically well below
#              the national average that CPM12 records.
#
# multiplier: applied to the raw CPM12 value before scaling/storing.
#   1.0 for most items; 2.0 for eggs because CPM12 is per-half-dozen and we
#   track per-dozen.
# ---------------------------------------------------------------------------
CPM12_ITEMS: dict[str, tuple[str, str, float]] = {
    # Petrol: national average pump price is what any driver pays → use directly
    "ie_petrol":  ("30190", "direct",  1.0),
    # Groceries: scale CPM12 trend to current Lidl price so the series is
    # Lidl-anchored but follows the real national inflation pattern
    "ie_bread":   ("10040", "scaled",  1.0),  # white sliced pan 800g
    "ie_milk":    ("10630", "scaled",  1.0),  # full fat milk 2L
    "ie_eggs":    ("10730", "scaled",  2.0),  # large eggs per half dozen → ×2
    "ie_chicken": ("10370", "scaled",  1.0),  # whole chicken 1.6kg trend
    "ie_mince":   ("10310", "scaled",  1.0),  # sliced/diced beef trend
    "ie_beer":    ("11760", "scaled",  1.0),  # lager 50cl can trend
}


# ---------------------------------------------------------------------------
# JSON-stat 2.0 parser helpers
# ---------------------------------------------------------------------------

def _parse_jsonstat(data: dict) -> tuple[list[str], list[int], dict, list]:
    """Return (id_list, sizes, dimension_indices, values) from a JSON-stat 2.0 response."""
    id_list = data["id"]
    sizes = data["size"]

    # Build dim_index: {dim_name: {category_code: position}}
    dim_index: dict[str, dict[str, int]] = {}
    for dim_name in id_list:
        cat = data["dimension"][dim_name]["category"]
        # JSON-stat index is a list of codes in order → convert to {code: position}
        idx = cat["index"]
        if isinstance(idx, list):
            dim_index[dim_name] = {code: pos for pos, code in enumerate(idx)}
        else:
            # Fallback: dict of code→int (older JSON-stat format)
            dim_index[dim_name] = {k: int(v) for k, v in idx.items()}

    values = data["value"]
    return id_list, sizes, dim_index, values


def _get_value(
    id_list: list[str],
    sizes: list[int],
    dim_index: dict[str, dict[str, int]],
    values: list,
    **lookup: str,
) -> float | None:
    """
    Look up a single value from the flat JSON-stat array using named dimension keys.

    Example:
        _get_value(id_list, sizes, dim_index, values,
                   STATISTIC="CPM12", TLIST="202001", C02363V03422="10040")
    """
    flat_idx = 0
    stride = 1
    for dim_name, size in zip(reversed(id_list), reversed(sizes)):
        code = lookup.get(dim_name, "")
        pos = dim_index[dim_name].get(code)
        if pos is None:
            return None
        flat_idx += pos * stride
        stride *= size

    val = values[flat_idx]
    return float(val) if val is not None else None


# ---------------------------------------------------------------------------
# Fetch CPM12 data
# ---------------------------------------------------------------------------

async def _fetch_cpm12() -> dict[str, dict[str, float]]:
    """
    Fetch CPM12 and return {item_code: {period_label_YYYY-MM: price_eur}}.

    Only months in 2020 or later are returned (matches our 5-year window).
    """
    logger.info("Fetching CPM12 (National Average Prices) from CSO...")
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(CSO_CPM12_URL)
        r.raise_for_status()
        data = r.json()

    id_list, sizes, dim_index, values = _parse_jsonstat(data)

    # Find the dimension names for month and item
    month_dim = "TLIST(M1)"
    item_dim = "C02363V03422"
    stat_dim = "STATISTIC"

    # Determine the statistic code (only one: "CPM12")
    stat_code = list(dim_index[stat_dim].keys())[0]

    # Collect month keys that are in range (2020-01 onwards)
    month_keys = dim_index[month_dim].keys()

    result: dict[str, dict[str, float]] = {}

    for month_key in month_keys:
        # month_key format: "202001" → period_label "2020-01"
        year = int(month_key[:4])
        month = int(month_key[4:])
        if year < 2020:
            continue
        period_label = f"{year}-{month:02d}"

        for item_code in CPM12_ITEMS:
            # Get the actual CPM12 item code (e.g. "10040")
            cpm12_code = CPM12_ITEMS[item_code][0]

            val = _get_value(
                id_list, sizes, dim_index, values,
                **{stat_dim: stat_code, month_dim: month_key, item_dim: cpm12_code},
            )
            if val is None:
                continue

            result.setdefault(item_code, {})[period_label] = val

    # Log a brief summary
    for slug, months in result.items():
        logger.info("  CPM12: %s — %d months loaded", slug, len(months))

    return result


# ---------------------------------------------------------------------------
# Fetch CPM01 food CPI for ie_coffee trend
# ---------------------------------------------------------------------------

async def _fetch_food_cpi() -> dict[str, float]:
    """
    Fetch CPM01 "Food and non-alcoholic beverages" CPI (Base Dec 2016=100).

    Returns {period_label_YYYY-MM: index_value} for 2020-01 onwards.
    """
    logger.info("Fetching CPM01 (Food CPI) from CSO for coffee trend...")
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(CSO_CPM01_URL)
        r.raise_for_status()
        data = r.json()

    id_list, sizes, dim_index, values = _parse_jsonstat(data)

    # Identify the correct dimension names from the actual response
    stat_dim = "STATISTIC"
    geo_dim = next(d for d in id_list if d not in (stat_dim, "TLIST(M1)"))
    month_dim = "TLIST(M1)"

    # We want: Consumer Price Index (Base Dec 2016=100), Food category
    # Find the stat code for "Base Dec 2016=100"
    stat_code = next(
        (k for k, v in data["dimension"][stat_dim]["category"]["label"].items()
         if "2016" in v and "Consumer Price Index" in v),
        list(dim_index[stat_dim].keys())[0],  # fallback: first stat
    )

    # Find the category code for food
    food_code = next(
        (k for k, v in data["dimension"][geo_dim]["category"]["label"].items()
         if "food" in v.lower() or "Food" in v),
        None,
    )
    if food_code is None:
        logger.warning("Could not find food category in CPM01 — coffee will use raw values")
        food_code = list(dim_index[geo_dim].keys())[0]

    result: dict[str, float] = {}
    for month_key, month_pos in dim_index[month_dim].items():
        # CPM01 month format: "2020 January" (label) but key is the actual key
        # The key itself might be like "202001" or the label format
        # From probe: labels are "2020 January" etc., keys look like positions
        # Let's check the actual key format
        month_label = data["dimension"][month_dim]["category"]["label"].get(month_key, "")
        if not month_label:
            continue
        # Parse "2020 January" → (2020, 1)
        try:
            parts = month_label.split()
            year = int(parts[0])
            month_name = parts[1]
        except (IndexError, ValueError):
            continue
        if year < 2020:
            continue
        month_map = {
            "January": 1, "February": 2, "March": 3, "April": 4,
            "May": 5, "June": 6, "July": 7, "August": 8,
            "September": 9, "October": 10, "November": 11, "December": 12,
        }
        month_num = month_map.get(month_name)
        if month_num is None:
            continue
        period_label = f"{year}-{month_num:02d}"

        val = _get_value(
            id_list, sizes, dim_index, values,
            **{stat_dim: stat_code, geo_dim: food_code, month_dim: month_key},
        )
        if val is not None:
            result[period_label] = val

    logger.info("  CPM01: food CPI — %d months loaded", len(result))
    return result


# ---------------------------------------------------------------------------
# Upsert helper
# ---------------------------------------------------------------------------

async def _upsert(db, good_id: int, price: float, period_label: str, collected_at: datetime) -> None:
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


# ---------------------------------------------------------------------------
# Main backfill function
# ---------------------------------------------------------------------------

async def backfill_ie_cso() -> None:
    """
    Backfill Irish prices using CSO CPM12 actual price data and CPM01 food CPI.
    Replaces approximate static estimates with official government data.
    """
    cpm12_data = await _fetch_cpm12()
    food_cpi = await _fetch_food_cpi()

    async with AsyncSessionLocal() as db:
        for slug, (cpm12_code, mode, multiplier) in CPM12_ITEMS.items():
            result = await db.execute(select(Good).where(Good.slug == slug))
            good = result.scalars().first()
            if not good:
                logger.warning("Good '%s' not found in database.", slug)
                continue

            monthly = cpm12_data.get(slug, {})
            if not monthly:
                logger.warning("No CPM12 data found for %s (code %s)", slug, cpm12_code)
                continue

            if mode == "scaled":
                # Get most recent CPM12 price (after applying multiplier) to use as
                # scaling denominator so we can anchor to the current Lidl price
                latest_period = max(monthly.keys())
                latest_cpm12_adjusted = monthly[latest_period] * multiplier

                # Get current Lidl price from DB (most recent snapshot)
                snap_result = await db.execute(
                    select(PriceSnapshot)
                    .where(PriceSnapshot.good_id == good.id)
                    .order_by(PriceSnapshot.collected_at.desc())
                    .limit(1)
                )
                latest_snap = snap_result.scalars().first()

                if latest_snap and latest_cpm12_adjusted > 0:
                    scale = latest_snap.price_usd / latest_cpm12_adjusted
                    logger.info(
                        "  %s: scaling CPM12×%.1f (%.2f) → Lidl (%.2f), factor=%.3f",
                        slug, multiplier, latest_cpm12_adjusted,
                        latest_snap.price_usd, scale,
                    )
                else:
                    scale = 1.0
                    logger.warning("  %s: no current DB price found, using scale=1.0", slug)
            else:
                scale = 1.0

            inserted = 0
            for period_label, cpm12_price in monthly.items():
                adjusted = cpm12_price * multiplier
                if mode == "direct":
                    price = round(adjusted, 2)
                else:  # "scaled"
                    price = round(adjusted * scale, 2)

                year, month = int(period_label[:4]), int(period_label[5:])
                collected_at = datetime(year, month, 1)
                await _upsert(db, good.id, price, period_label, collected_at)
                inserted += 1

            await db.commit()
            logger.info(
                "  %-12s %d months upserted (mode=%s, source=CPM12:%s)",
                slug, inserted, mode, cpm12_code,
            )

        # --- ie_coffee: use CPM01 food CPI trend ---
        result = await db.execute(select(Good).where(Good.slug == "ie_coffee"))
        coffee = result.scalars().first()
        if coffee and food_cpi:
            snap_result = await db.execute(
                select(PriceSnapshot)
                .where(PriceSnapshot.good_id == coffee.id)
                .order_by(PriceSnapshot.collected_at.desc())
                .limit(1)
            )
            latest_snap = snap_result.scalars().first()
            current_price = latest_snap.price_usd if latest_snap else 3.99

            latest_period = max(food_cpi.keys())
            latest_cpi = food_cpi[latest_period]

            inserted = 0
            for period_label, cpi_val in food_cpi.items():
                if latest_cpi > 0:
                    price = round(current_price * (cpi_val / latest_cpi), 2)
                else:
                    price = current_price

                year, month = int(period_label[:4]), int(period_label[5:])
                collected_at = datetime(year, month, 1)
                await _upsert(db, coffee.id, price, period_label, collected_at)
                inserted += 1

            await db.commit()
            logger.info(
                "  %-12s %d months upserted (mode=cpi_scaled, source=CPM01 food)",
                "ie_coffee", inserted,
            )
        else:
            logger.warning("ie_coffee: skipped (good not found or no CPI data)")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    logger.info("============================================================")
    logger.info("Real Life Index — CSO Ireland Price Backfill")
    logger.info("============================================================")
    logger.info("Source: CSO StatBank CPM12 (National Average Prices, EUR)")
    logger.info("")

    await init_db()
    async with AsyncSessionLocal() as db:
        await seed_goods(db)

    await backfill_ie_cso()

    logger.info("============================================================")
    logger.info("CSO backfill complete.")
    logger.info("Ireland grocery and fuel history now based on official data.")
    logger.info("============================================================")


if __name__ == "__main__":
    asyncio.run(main())
