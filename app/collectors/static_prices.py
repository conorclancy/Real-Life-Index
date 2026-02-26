"""
Static / manually-maintained prices for goods that have no public API.

WHY STATIC?
  - McDonald's: no public pricing API; regional variation; website requires
    JavaScript rendering and blocks scrapers. Prices change ~1-2x per year.
  - Netflix: flat US subscription rate; changes rarely (a few times per year);
    no machine-readable pricing endpoint.

HOW TO UPDATE:
  When you notice a price change, update the value below and add a comment
  with the date you verified it. The next scheduled refresh will pick it up.
"""

# fmt: off
STATIC_PRICES: dict[str, float] = {
    # McDonald's Big Mac Extra Value Meal (medium fries + medium drink)
    # Source: menu price checks / BigMacIndex.org
    # US national average — regional prices vary by ~$1-2
    # Last verified: Q1 2025
    "mcdonalds": 9.29,

    # Netflix Standard plan (1080p, 2 simultaneous streams)
    # Source: netflix.com/signup — US pricing
    # Last verified: Q1 2025
    "netflix": 17.99,
}
# fmt: on


def get_static_price(slug: str) -> float | None:
    """
    Return the manually-maintained price for a given good slug.
    Returns None if the slug is not in the static price list.
    """
    return STATIC_PRICES.get(slug)
