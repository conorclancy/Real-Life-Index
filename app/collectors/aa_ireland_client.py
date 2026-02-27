"""
AA Ireland fuel price scraper.

Fetches the current average unleaded petrol price in Ireland (€/litre) from
the AA Ireland Fuel Tracker page:
  https://www.aaireland.ie/AA/Motoring/Motoring-news/Fuel-tracker/

The AA Ireland website is a fairly standard server-rendered page. We scrape it
with httpx and use regex to extract the current average ROI petrol price.

If the page structure changes or the request fails, this collector returns None
and the dashboard retains the last stored price.

No API key required.
"""

import logging
import re
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

AA_FUEL_TRACKER_URL = (
    "https://www.aaireland.ie/AA/Motoring/Motoring-news/Fuel-tracker/"
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IE,en;q=0.9",
}


class AAIrelandClient:
    """Scrapes the current average petrol price (€/litre) from AA Ireland."""

    async def get_petrol_price(self) -> tuple[float, str] | None:
        """
        Fetch the current average unleaded petrol price in Ireland.

        Returns:
            (price_eur_per_litre, period_label) or None on failure.
        """
        try:
            async with httpx.AsyncClient(
                headers=_HEADERS, timeout=20.0, follow_redirects=True
            ) as client:
                response = await client.get(AA_FUEL_TRACKER_URL)
                response.raise_for_status()
                html = response.text
        except httpx.HTTPError as exc:
            logger.warning("AA Ireland: HTTP error: %s", exc)
            return None

        price = _extract_petrol_price(html)
        if price is None:
            logger.warning(
                "AA Ireland: could not extract petrol price. "
                "The page structure may have changed."
            )
            return None

        period_label = datetime.utcnow().strftime("%Y-%m")
        logger.info("  AA IE: petrol = €%.3f/litre (%s)", price, period_label)
        return (price, period_label)


def _extract_petrol_price(html: str) -> float | None:
    """
    Extract the ROI average unleaded petrol price from the AA Ireland
    fuel tracker page HTML.

    The AA Ireland page publishes prices as "XXX.Xc" (cent per litre) or
    "€X.XXX" format — we try multiple patterns and convert to €/litre.
    """
    # Pattern 1: price in cent format e.g. "171.4c" or "171.4 c/litre"
    patterns_cent = [
        r'unleaded[^<]{0,200}?(\d{3}(?:\.\d)?)\s*[cC]',
        r'petrol[^<]{0,200}?(\d{3}(?:\.\d)?)\s*[cC]',
        r'(\d{3}(?:\.\d)?)\s*[cC](?:/litre|c/l|\s)',
    ]
    for pattern in patterns_cent:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            try:
                cent_value = float(match.group(1))
                if 100 < cent_value < 250:  # Sanity: 100c–250c per litre
                    return round(cent_value / 100, 3)
            except ValueError:
                continue

    # Pattern 2: price in euro format e.g. "€1.714" or "1.714 €/l"
    patterns_euro = [
        r'€\s*(\d\.\d{2,3})',
        r'(\d\.\d{3})\s*€',
        r'"price"\s*:\s*"?(\d\.\d{2,3})"?',
    ]
    for pattern in patterns_euro:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            try:
                euro_value = float(match.group(1))
                if 0.8 < euro_value < 3.0:  # Sanity: €0.80–€3.00 per litre
                    return euro_value
            except ValueError:
                continue

    return None
