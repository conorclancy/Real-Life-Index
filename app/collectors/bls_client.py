"""
BLS (Bureau of Labor Statistics) API v2 client.

Fetches Average Retail Prices for grocery items using the BLS public API.
Covers 7 of the 10 tracked goods: eggs, milk, bread, ground beef, chicken,
coffee, and beer.

API docs: https://www.bls.gov/developers/api_python.htm
Series finder: https://data.bls.gov/cgi-bin/browse.pl?prefix=AP (Average Retail Prices)

Requires a free API key from: https://data.bls.gov/registrationEngine/
"""

import logging
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

BLS_API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"


class BLSClient:
    """Async client for the BLS Average Retail Prices API (v2)."""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        if not api_key:
            logger.warning(
                "BLS_API_KEY is not set. Requests will be unauthenticated "
                "(limit: 25/day, max 3 years of data). "
                "Register free at https://data.bls.gov/registrationEngine/"
            )

    async def fetch_series(
        self,
        series_ids: list[str],
        start_year: str,
        end_year: str,
    ) -> dict[str, list[dict]]:
        """
        POST to the BLS API v2 and return the raw data for each series.

        Args:
            series_ids: List of BLS series IDs (e.g. ["APU0000708111"])
            start_year: Four-digit year string, e.g. "2020"
            end_year:   Four-digit year string, e.g. "2025"

        Returns:
            Dict mapping series_id -> list of data point dicts.
            Each data point has keys: year, period, periodName, value, ...
            Returns an empty dict on any error.
        """
        payload: dict = {
            "seriesid": series_ids,
            "startyear": start_year,
            "endyear": end_year,
        }
        # Only include the key if we have one (unauthenticated still works)
        if self.api_key:
            payload["registrationkey"] = self.api_key

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(BLS_API_URL, json=payload)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("BLS API HTTP error: %s", exc)
            return {}

        data = response.json()

        if data.get("status") != "REQUEST_SUCCEEDED":
            logger.error(
                "BLS API request failed. Status: %s | Messages: %s",
                data.get("status"),
                data.get("message"),
            )
            return {}

        # Build result dict: series_id -> list of data points
        result: dict[str, list[dict]] = {}
        for series in data.get("Results", {}).get("series", []):
            result[series["seriesID"]] = series.get("data", [])

        return result

    async def get_latest_prices(
        self, series_ids: list[str]
    ) -> dict[str, tuple[float, str]]:
        """
        Fetch the most recent price for each series ID in a single API call.

        Returns:
            Dict mapping series_id -> (price_float, period_label).
            period_label format: "YYYY-MM" (e.g. "2024-12")
            Returns empty dict on failure.
        """
        current_year = str(datetime.now().year)
        prior_year = str(datetime.now().year - 1)

        all_data = await self.fetch_series(series_ids, prior_year, current_year)
        if not all_data:
            return {}

        result: dict[str, tuple[float, str]] = {}
        for series_id, data_points in all_data.items():
            if not data_points:
                logger.warning("No data points returned for series %s", series_id)
                continue

            # BLS returns data newest-first; take the first valid numeric value
            for point in data_points:
                value_str = point.get("value", "")
                if value_str in ("", "-", "N/A"):
                    continue
                try:
                    price = float(value_str)
                except ValueError:
                    continue

                period_label = _bls_period_to_label(point["year"], point["period"])
                result[series_id] = (price, period_label)
                break  # We only want the most recent

        return result

    async def get_historical_prices(
        self, series_id: str, years: int = 5
    ) -> list[tuple[str, float]]:
        """
        Fetch full price history for a single series over the past N years.

        Returns:
            List of (period_label, price) tuples sorted oldest-first.
            period_label format: "YYYY-MM"
        """
        end_year = datetime.now().year
        start_year = end_year - years

        all_data = await self.fetch_series(
            [series_id], str(start_year), str(end_year)
        )
        data_points = all_data.get(series_id, [])

        history: list[tuple[str, float]] = []
        for point in data_points:
            # Skip annual averages (period "M13") and non-monthly entries
            if not point["period"].startswith("M") or point["period"] == "M13":
                continue
            value_str = point.get("value", "")
            if value_str in ("", "-", "N/A"):
                continue
            try:
                price = float(value_str)
            except ValueError:
                continue

            label = _bls_period_to_label(point["year"], point["period"])
            history.append((label, price))

        # BLS returns newest-first; reverse to oldest-first for charting
        history.reverse()
        return history


def _bls_period_to_label(year: str, period: str) -> str:
    """
    Convert BLS year + period to a sortable YYYY-MM string.

    BLS periods look like "M01" (January) through "M12" (December).
    Example: year="2024", period="M12" -> "2024-12"
    """
    month = period.lstrip("M")          # "M12" -> "12"
    return f"{year}-{month.zfill(2)}"   # -> "2024-12"
