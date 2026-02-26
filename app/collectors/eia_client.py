"""
EIA (U.S. Energy Information Administration) Open Data API client.

Fetches weekly US national average regular gasoline retail prices ($/gallon).
This covers the "gasoline" good in our tracked basket.

API docs: https://www.eia.gov/opendata/
Series: US Regular Conventional Gas Price, Dollars per Gallon
        Product: EPM0 (Regular Gasoline)
        Area:    NUS  (US National Average)

No API key is required for basic use. An optional free key can be registered
at https://www.eia.gov/opendata/register.php for higher rate limits.
"""

import logging
from datetime import datetime, timedelta

import httpx

logger = logging.getLogger(__name__)

EIA_API_URL = "https://api.eia.gov/v2/petroleum/pri/gnd/data/"


class EIAClient:
    """Async client for the EIA Open Data petroleum prices API."""

    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key

    async def get_latest_gasoline_price(self) -> tuple[float, str] | None:
        """
        Fetch the most recent weekly US national average regular gasoline price.

        Returns:
            (price_usd, period_label) where period_label is "YYYY-WNN" format,
            or None on failure.
        """
        # Request the last 4 weeks of data and take the most recent
        params: dict = {
            "frequency": "weekly",
            "data[0]": "value",
            "facets[product][]": "EPM0",   # Regular gasoline
            "facets[duoarea][]": "NUS",     # US national average
            "sort[0][column]": "period",
            "sort[0][direction]": "desc",
            "length": "4",
            "offset": "0",
        }
        if self.api_key:
            params["api_key"] = self.api_key

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(EIA_API_URL, params=params)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("EIA API HTTP error: %s", exc)
            return None

        data = response.json()

        rows = data.get("response", {}).get("data", [])
        if not rows:
            logger.error("EIA API returned no data rows. Response: %s", data)
            return None

        # Rows are sorted newest-first; find the first row with a valid value
        for row in rows:
            value = row.get("value")
            period = row.get("period", "")
            if value is None:
                continue
            try:
                price = float(value)
            except (TypeError, ValueError):
                continue

            # EIA period format is "YYYY-MM-DD" (week ending date)
            # Convert to our label format "YYYY-WNN"
            period_label = _eia_period_to_label(period)
            return (price, period_label)

        logger.error("EIA API: no valid price value found in response rows")
        return None

    async def get_historical_gasoline_prices(
        self, weeks: int = 260
    ) -> list[tuple[str, float]]:
        """
        Fetch weekly gasoline price history for the past N weeks (~5 years default).

        Returns:
            List of (period_label, price) tuples sorted oldest-first.
        """
        # Calculate start date
        start_date = (datetime.now() - timedelta(weeks=weeks)).strftime("%Y-%m-%d")
        end_date = datetime.now().strftime("%Y-%m-%d")

        params: dict = {
            "frequency": "weekly",
            "data[0]": "value",
            "facets[product][]": "EPM0",
            "facets[duoarea][]": "NUS",
            "sort[0][column]": "period",
            "sort[0][direction]": "asc",
            "start": start_date,
            "end": end_date,
            "length": "5000",  # Well above 260 weeks
        }
        if self.api_key:
            params["api_key"] = self.api_key

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(EIA_API_URL, params=params)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("EIA API HTTP error (historical): %s", exc)
            return []

        data = response.json()
        rows = data.get("response", {}).get("data", [])

        history: list[tuple[str, float]] = []
        for row in rows:
            value = row.get("value")
            period = row.get("period", "")
            if value is None:
                continue
            try:
                price = float(value)
            except (TypeError, ValueError):
                continue
            history.append((_eia_period_to_label(period), price))

        return history


def _eia_period_to_label(period: str) -> str:
    """
    Convert EIA period string (YYYY-MM-DD week-ending date) to a label.

    We keep the full date string as our label since EIA data is weekly
    and the date is the most meaningful identifier.
    Example: "2024-12-30" -> "2024-12-30"
    """
    # Return as-is — ISO date strings sort correctly and are readable
    return period
