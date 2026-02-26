"""
Pydantic schemas for API JSON responses.
These define the shape of data returned by the /api/* endpoints.
"""

from datetime import datetime

from pydantic import BaseModel


class SnapshotSchema(BaseModel):
    """A single price observation — used in history endpoints."""
    period_label: str
    price_usd: float
    collected_at: datetime

    model_config = {"from_attributes": True}


class GoodSummarySchema(BaseModel):
    """
    Summary of a good's current state — used on the dashboard home page.
    Includes computed fields (pct_change, sparkline data) added by price_service.
    """
    slug: str
    name: str
    unit: str
    category: str
    emoji: str
    latest_price: float | None
    latest_period: str | None
    price_1y_ago: float | None        # Price ~12 months ago (for % change)
    pct_change_1y: float | None       # % change vs 12 months ago
    sparkline: list[float]            # Last 12 price values for the mini chart

    model_config = {"from_attributes": True}


class GoodDetailSchema(BaseModel):
    """Full detail for a single good — used on the detail page."""
    slug: str
    name: str
    unit: str
    category: str
    source: str
    emoji: str
    latest_price: float | None
    latest_period: str | None
    price_1m_ago: float | None
    price_6m_ago: float | None
    price_1y_ago: float | None
    pct_change_1m: float | None
    pct_change_6m: float | None
    pct_change_1y: float | None
    all_time_high: float | None
    all_time_low: float | None
    history: list[SnapshotSchema]

    model_config = {"from_attributes": True}


class RefreshStatusSchema(BaseModel):
    """Response from the /admin/refresh endpoint."""
    message: str
    triggered_at: datetime
