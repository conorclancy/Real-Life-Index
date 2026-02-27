"""
Lidl Ireland price scraper.

Lidl.ie uses Nuxt 3 (Vue), not Next.js. Product data for search results is
embedded directly in the page HTML inside a large ShallowReactive script tag.

Strategy (tries each in order, stops at first success):

  1. Nuxt payload extraction — finds the ShallowReactive hydration script and
     looks for the first RETAIL (non-Lidl-Plus) product price. Regular prices
     appear in the data as:  <price_float>,"white_red"
     Lidl Plus / sale prices appear as:  <price_float>,"red_yellow"
     We prefer "white_red" (regular retail) prices.

  2. Unit-price pattern — if no white_red price is found, falls back to finding
     prices that appear immediately after a weight/unit string:
     e.g.  "500g",1.69  or  "each",3.49

  3. EUR currency marker pattern — looks for decimal values near "EUR" markers
     in the serialised state.

The /q/search URL was confirmed from lidl.ie/robots.txt:
  Disallow: /q/search?id=*

If all strategies fail, returns None and the dashboard retains the last
stored price until the next successful fetch. No API key required.
"""

import logging
import re
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

LIDL_IE_BASE = "https://www.lidl.ie"
LIDL_IE_SEARCH = f"{LIDL_IE_BASE}/q/search"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IE,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}

# Regex to find the Nuxt 3 ShallowReactive hydration payload script.
# It's the largest script tag and starts with [["ShallowReactive"
_NUXT_PAYLOAD_RE = re.compile(
    r'<script[^>]*>\s*(\[\["ShallowReactive".*?)\s*</script>',
    re.DOTALL,
)

# Regular (non-Lidl-Plus) retail price: appears as  <float>,"white_red"
_RETAIL_PRICE_RE = re.compile(r'(\d+\.\d{2}),"white_red"')

# Price after a unit/weight string: "500g",1.69  or  "each",3.49
_UNIT_PRICE_RE = re.compile(
    r'"(?:each|[\d./]+\s*(?:g|kg|ml|l|L|G|K|M|cl))",\s*(\d+\.\d{2})'
)

# Price near "EUR" currency marker in the serialised state
_EUR_MARKER_RE = re.compile(
    r'"EUR","",(?:"[^"]*",){0,4}(\d+\.\d{2})'
)


class LidlIEClient:
    """Scrapes current grocery prices from Lidl Ireland (lidl.ie)."""

    async def get_price(self, slug: str, search_term: str) -> tuple[float, str] | None:
        """
        Search for a product on Lidl.ie and return its current price.

        Returns:
            (price_eur, period_label) or None on failure.
        """
        try:
            async with httpx.AsyncClient(
                timeout=20.0, follow_redirects=True
            ) as client:
                response = await client.get(
                    LIDL_IE_SEARCH, params={"q": search_term}, headers=_HEADERS
                )
                response.raise_for_status()
                html = response.text
        except httpx.HTTPError as exc:
            logger.warning("Lidl IE: HTTP error fetching '%s': %s", slug, exc)
            return None

        price = _extract_price_from_nuxt(html, slug)

        if price is None:
            logger.warning(
                "Lidl IE: could not extract price for '%s' (search: %r). "
                "The site structure may have changed or the product is unavailable.",
                slug, search_term,
            )
            return None

        period_label = datetime.utcnow().strftime("%Y-%m")
        logger.info("  Lidl IE: %s = EUR %.2f (%s)", slug, price, period_label)
        return (price, period_label)


def _extract_price_from_nuxt(html: str, slug: str) -> float | None:
    """
    Extract the first product price from Lidl.ie's Nuxt 3 hydration payload.

    The product data is embedded as a ShallowReactive script tag.
    Prices for regular (non-Lidl-Plus) items appear as: <float>,"white_red"
    """
    # Find the Nuxt payload script
    match = _NUXT_PAYLOAD_RE.search(html)
    if not match:
        logger.debug("Lidl IE (%s): Nuxt ShallowReactive payload not found.", slug)
        return _fallback_any_price(html, slug)

    payload = match.group(1)

    # Strategy 1: regular retail price (white_red = standard price tag colour)
    m = _RETAIL_PRICE_RE.search(payload)
    if m:
        price = float(m.group(1))
        if _plausible(price):
            logger.debug("Lidl IE (%s): found retail price %.2f via white_red marker", slug, price)
            return price

    # Strategy 2: price immediately after a weight/unit string
    m = _UNIT_PRICE_RE.search(payload)
    if m:
        price = float(m.group(1))
        if _plausible(price):
            logger.debug("Lidl IE (%s): found price %.2f via unit-price pattern", slug, price)
            return price

    # Strategy 3: price near EUR currency marker
    m = _EUR_MARKER_RE.search(payload)
    if m:
        price = float(m.group(1))
        if _plausible(price):
            logger.debug("Lidl IE (%s): found price %.2f via EUR marker", slug, price)
            return price

    logger.debug("Lidl IE (%s): no price found in Nuxt payload.", slug)
    return _fallback_any_price(html, slug)


def _fallback_any_price(html: str, slug: str) -> float | None:
    """
    Last-resort: scan the full HTML for any plausible euro price value.
    Avoids the large integer column-index values in the Nuxt schema map.
    """
    # Only match decimal numbers (X.XX format) in a plausible grocery price range
    for m in re.finditer(r'\b(\d{1,2}\.\d{2})\b', html):
        val = float(m.group(1))
        if _plausible(val):
            logger.debug("Lidl IE (%s): found price %.2f via decimal fallback", slug, val)
            return val

    logger.debug("Lidl IE (%s): no price found via fallback.", slug)
    return None


def _plausible(price: float) -> bool:
    """Return True if the value is a plausible grocery price (€0.20 – €50)."""
    return 0.20 < price < 50.0
