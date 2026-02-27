"""
Static / manually-maintained prices for goods that have no public API.

WHY STATIC?
  - McDonald's: no public pricing API; regional variation; website requires
    JavaScript rendering and blocks scrapers. Prices change ~1-2x per year.
  - Netflix: flat subscription rate; changes rarely; no machine-readable pricing endpoint.
  - Supermac's (IE): no public pricing API; menu prices verified manually.

HOW TO UPDATE:
  When a price changes, update the value below and note the date.

HOW TO UPDATE:
  When you notice a price change, update the value below and add a comment
  with the date you verified it. The next scheduled refresh will pick it up.
"""

# fmt: off
STATIC_PRICES: dict[str, float] = {
    # ── United States ────────────────────────────────────────────────────────
    # McDonald's Big Mac Extra Value Meal (medium fries + medium drink)
    # US national average — regional prices vary by ~$1-2
    # Last verified: Q1 2025
    "mcdonalds": 9.29,

    # Netflix Standard plan (1080p, 2 simultaneous streams) — US pricing
    # Last verified: Q1 2025
    "netflix": 17.99,

    # ── Ireland ──────────────────────────────────────────────────────────────
    # Supermac's Regular Meal (burger + regular chips + regular drink)
    # Source: supermacs.ie menu
    # Last verified: Q1 2025
    "ie_supermacs": 10.99,

    # Netflix Standard plan (1080p, 2 simultaneous streams) — Ireland/EUR pricing
    # Source: netflix.com/ie
    # Last verified: Q1 2025
    "ie_netflix": 15.99,
}
# fmt: on


def get_static_price(slug: str) -> float | None:
    """
    Return the manually-maintained price for a given good slug.
    Returns None if the slug is not in the static price list.
    """
    return STATIC_PRICES.get(slug)
