"""
FX rate service — fetches a live USD/EUR exchange rate.

Source: frankfurter.app (European Central Bank data, updated daily on business days).
No API key required.

Results are cached in-process for 1 hour to avoid hammering the external API
on every page load.  A hardcoded fallback is used if the request fails.
"""

import logging
import time

import httpx

logger = logging.getLogger(__name__)

# Frankfurter.app: returns EUR per 1 USD
_URL = "https://api.frankfurter.app/latest?from=USD&to=EUR"

# Fallback rate used when the API is unreachable (update occasionally)
_FALLBACK_RATE: float = 0.92
_FALLBACK_DATE: str = "fallback"

# Simple in-process cache (avoids one HTTP call per page load)
_cached_rate: float | None = None
_cached_date: str | None = None
_cache_ts: float = 0.0
_CACHE_TTL: float = 3600.0  # seconds


async def fetch_eur_per_usd() -> tuple[float, str]:
    """
    Return (eur_per_usd, date_string).

    Example: (0.9234, "2025-03-01") means 1 USD = €0.9234 on that date.
    Falls back to (_FALLBACK_RATE, "fallback") if the request fails.
    """
    global _cached_rate, _cached_date, _cache_ts

    now = time.monotonic()
    if _cached_rate is not None and (now - _cache_ts) < _CACHE_TTL:
        return _cached_rate, _cached_date  # type: ignore[return-value]

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(_URL)
            resp.raise_for_status()
            data = resp.json()

        rate = float(data["rates"]["EUR"])
        date = str(data.get("date", ""))

        _cached_rate = round(rate, 6)
        _cached_date = date
        _cache_ts = now

        logger.debug("FX rate fetched: 1 USD = %.4f EUR (%s)", rate, date)
        return _cached_rate, _cached_date

    except Exception as exc:
        logger.warning("FX fetch failed (%s) — using fallback %.4f", exc, _FALLBACK_RATE)
        return _FALLBACK_RATE, _FALLBACK_DATE
