"""
Price service — database query helpers and Plotly chart builders.

This module sits between the database and the route handlers, providing
clean, typed data that routes can pass directly to templates or return as JSON.
"""

import json
import logging
from datetime import datetime, timedelta

import plotly.graph_objects as go
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Good, PriceSnapshot
from app.schemas import GoodDetailSchema, GoodSummarySchema, SnapshotSchema

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

async def get_all_goods(db: AsyncSession) -> list[Good]:
    """Return all goods, ordered by category then name."""
    result = await db.execute(
        select(Good).order_by(Good.category, Good.name)
    )
    return list(result.scalars().all())


async def get_goods_by_country(db: AsyncSession, country: str) -> list[Good]:
    """Return goods for a specific country ('us' or 'ie'), ordered by category then name."""
    result = await db.execute(
        select(Good)
        .where(Good.country == country)
        .order_by(Good.category, Good.name)
    )
    return list(result.scalars().all())


async def get_good_by_slug(db: AsyncSession, slug: str) -> Good | None:
    """Return a single Good by slug, or None if not found."""
    result = await db.execute(select(Good).where(Good.slug == slug))
    return result.scalars().first()


async def get_snapshots_for_good(
    db: AsyncSession, good_id: int, months: int = 60
) -> list[PriceSnapshot]:
    """
    Return price snapshots for a good over the past N months,
    sorted oldest-first (ready for charting).
    """
    since = datetime.utcnow() - timedelta(days=months * 30)
    result = await db.execute(
        select(PriceSnapshot)
        .where(
            PriceSnapshot.good_id == good_id,
            PriceSnapshot.collected_at >= since,
        )
        .order_by(PriceSnapshot.collected_at.asc())
    )
    return list(result.scalars().all())


async def get_all_snapshots_for_good(
    db: AsyncSession, good_id: int
) -> list[PriceSnapshot]:
    """Return all snapshots for a good, sorted oldest-first."""
    result = await db.execute(
        select(PriceSnapshot)
        .where(PriceSnapshot.good_id == good_id)
        .order_by(PriceSnapshot.collected_at.asc())
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Summary builders (dashboard home)
# ---------------------------------------------------------------------------

async def build_good_summaries(
    db: AsyncSession, country: str = "us"
) -> list[GoodSummarySchema]:
    """
    Build summary data for goods in the given country — used to populate the
    dashboard cards. Includes current price, % change vs 1 year ago, and sparkline.
    """
    goods = await get_goods_by_country(db, country)
    summaries: list[GoodSummarySchema] = []

    for good in goods:
        # Get last 14 months of snapshots (gives us 12 for sparkline + 2 for comparison)
        snapshots = await get_snapshots_for_good(db, good.id, months=14)

        if not snapshots:
            summaries.append(GoodSummarySchema(
                slug=good.slug,
                name=good.name,
                unit=good.unit,
                category=good.category,
                emoji=good.emoji,
                country=good.country,
                currency=good.currency,
                latest_price=None,
                latest_period=None,
                price_1y_ago=None,
                pct_change_1y=None,
                sparkline=[],
            ))
            continue

        latest = snapshots[-1]
        latest_price = latest.price_usd
        latest_period = latest.period_label

        # Find the snapshot closest to 12 months ago
        one_year_ago = datetime.utcnow() - timedelta(days=365)
        price_1y_ago = _find_closest_price(snapshots, one_year_ago)

        pct_change_1y = None
        if price_1y_ago and price_1y_ago > 0:
            pct_change_1y = round(((latest_price - price_1y_ago) / price_1y_ago) * 100, 1)

        # Last 12 prices for the sparkline mini chart
        sparkline = [s.price_usd for s in snapshots[-12:]]

        summaries.append(GoodSummarySchema(
            slug=good.slug,
            name=good.name,
            unit=good.unit,
            category=good.category,
            emoji=good.emoji,
            country=good.country,
            currency=good.currency,
            latest_price=round(latest_price, 2),
            latest_period=latest_period,
            price_1y_ago=round(price_1y_ago, 2) if price_1y_ago else None,
            pct_change_1y=pct_change_1y,
            sparkline=sparkline,
        ))

    return summaries


# ---------------------------------------------------------------------------
# Detail builder (per-good detail page)
# ---------------------------------------------------------------------------

async def build_good_detail(
    db: AsyncSession, slug: str
) -> GoodDetailSchema | None:
    """
    Build the full detail view for a single good.
    Returns None if the good doesn't exist.
    """
    good = await get_good_by_slug(db, slug)
    if not good:
        return None

    snapshots = await get_all_snapshots_for_good(db, good.id)
    if not snapshots:
        return GoodDetailSchema(
            slug=good.slug, name=good.name, unit=good.unit,
            category=good.category, source=good.source, emoji=good.emoji,
            country=good.country, currency=good.currency,
            latest_price=None, latest_period=None,
            price_1m_ago=None, price_6m_ago=None, price_1y_ago=None,
            pct_change_1m=None, pct_change_6m=None, pct_change_1y=None,
            all_time_high=None, all_time_low=None, history=[],
        )

    latest_price = snapshots[-1].price_usd
    now = datetime.utcnow()

    price_1m = _find_closest_price(snapshots, now - timedelta(days=30))
    price_6m = _find_closest_price(snapshots, now - timedelta(days=182))
    price_1y = _find_closest_price(snapshots, now - timedelta(days=365))

    def pct(current: float, prior: float | None) -> float | None:
        if prior and prior > 0:
            return round(((current - prior) / prior) * 100, 1)
        return None

    all_prices = [s.price_usd for s in snapshots]
    history_schemas = [
        SnapshotSchema(
            period_label=s.period_label or "",
            price_usd=s.price_usd,
            collected_at=s.collected_at,
        )
        for s in snapshots
    ]

    return GoodDetailSchema(
        slug=good.slug,
        name=good.name,
        unit=good.unit,
        category=good.category,
        source=good.source,
        emoji=good.emoji,
        country=good.country,
        currency=good.currency,
        latest_price=round(latest_price, 2),
        latest_period=snapshots[-1].period_label,
        price_1m_ago=round(price_1m, 2) if price_1m else None,
        price_6m_ago=round(price_6m, 2) if price_6m else None,
        price_1y_ago=round(price_1y, 2) if price_1y else None,
        pct_change_1m=pct(latest_price, price_1m),
        pct_change_6m=pct(latest_price, price_6m),
        pct_change_1y=pct(latest_price, price_1y),
        all_time_high=round(max(all_prices), 2),
        all_time_low=round(min(all_prices), 2),
        history=history_schemas,
    )


# ---------------------------------------------------------------------------
# Chart builders (Plotly)
# ---------------------------------------------------------------------------

async def build_overview_chart_json(db: AsyncSession, country: str = "us") -> str:
    """
    Build the main overview chart for the dashboard home page.

    All goods for the given country are normalised to index = 100 at their
    earliest recorded price, so wildly different amounts can be compared on
    the same Y-axis.

    Returns a JSON string passed directly to Plotly.newPlot() in the template.
    """
    goods = await get_goods_by_country(db, country)
    traces = []

    for good in goods:
        snapshots = await get_all_snapshots_for_good(db, good.id)
        if len(snapshots) < 2:
            continue  # Not enough data to draw a meaningful line

        base_price = snapshots[0].price_usd
        if base_price == 0:
            continue

        x_dates = [s.period_label or s.collected_at.strftime("%Y-%m") for s in snapshots]
        y_index = [round((s.price_usd / base_price) * 100, 1) for s in snapshots]

        traces.append(go.Scatter(
            x=x_dates,
            y=y_index,
            mode="lines",
            name=f"{good.emoji} {good.name}",
            hovertemplate=(
                f"<b>{good.name}</b><br>"
                "Period: %{x}<br>"
                "Index: %{y:.1f}<br>"
                "<extra></extra>"
            ),
        ))

    layout = go.Layout(
        title=None,
        yaxis=dict(title="Price Index (Base = 100)", gridcolor="#e5e7eb"),
        xaxis=dict(title=None, gridcolor="#e5e7eb"),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=50, r=20, t=10, b=50),
    )

    fig = go.Figure(data=traces, layout=layout)
    return json.dumps(fig.to_dict())


def build_detail_chart_json(detail: GoodDetailSchema) -> str:
    """
    Build the full history chart for a single good's detail page.

    Returns a JSON string for Plotly.newPlot() in the template.
    """
    if not detail.history:
        return json.dumps({"data": [], "layout": {}})

    x_dates = [s.period_label for s in detail.history]
    y_prices = [s.price_usd for s in detail.history]

    currency_sym = "€" if getattr(detail, "currency", "USD") == "EUR" else "$"
    currency_code = getattr(detail, "currency", "USD")

    trace = go.Scatter(
        x=x_dates,
        y=y_prices,
        mode="lines+markers",
        name=detail.name,
        line=dict(color="#3b82f6", width=2),
        marker=dict(size=4),
        hovertemplate=(
            "Period: %{x}<br>"
            f"Price: {currency_sym}%{{y:.2f}} {detail.unit}<br>"
            "<extra></extra>"
        ),
    )

    layout = go.Layout(
        title=None,
        yaxis=dict(
            title=f"Price ({currency_code}, {detail.unit})",
            gridcolor="#e5e7eb",
            tickprefix=currency_sym,
        ),
        xaxis=dict(
            title=None,
            gridcolor="#e5e7eb",
            # Range selector buttons for easy time filtering
            rangeselector=dict(
                buttons=[
                    dict(count=1, label="1M", step="month", stepmode="backward"),
                    dict(count=6, label="6M", step="month", stepmode="backward"),
                    dict(count=1, label="1Y", step="year", stepmode="backward"),
                    dict(step="all", label="All"),
                ]
            ),
            rangeslider=dict(visible=True),
            type="date",
        ),
        hovermode="x unified",
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=60, r=20, t=10, b=50),
    )

    fig = go.Figure(data=[trace], layout=layout)
    return json.dumps(fig.to_dict())


def build_sparkline_json(prices: list[float]) -> str:
    """
    Build a tiny inline sparkline chart JSON for dashboard cards.
    Minimal styling — just a small line with no axes or labels.
    """
    if not prices:
        return json.dumps({"data": [], "layout": {}})

    trace = go.Scatter(
        y=prices,
        mode="lines",
        line=dict(
            color="#3b82f6" if prices[-1] >= prices[0] else "#ef4444",
            width=1.5,
        ),
        hoverinfo="skip",
    )

    layout = go.Layout(
        height=50,
        margin=dict(l=0, r=0, t=0, b=0),
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
    )

    fig = go.Figure(data=[trace], layout=layout)
    return json.dumps(fig.to_dict())


# ---------------------------------------------------------------------------
# Comparison builder (US vs Ireland side-by-side)
# ---------------------------------------------------------------------------

# Maps US slug → equivalent IE slug.  Order determines table row order.
_COMPARISON_PAIRS: list[tuple[str, str]] = [
    ("eggs",        "ie_eggs"),
    ("milk",        "ie_milk"),
    ("bread",       "ie_bread"),
    ("ground_beef", "ie_mince"),
    ("chicken",     "ie_chicken"),
    ("coffee",      "ie_coffee"),
    ("beer",        "ie_beer"),
    ("gasoline",    "ie_petrol"),
    ("mcdonalds",   "ie_supermacs"),
    ("netflix",     "ie_netflix"),
]


async def build_comparison_pairs(db: AsyncSession) -> list[dict]:
    """
    Return a list of {us, ie, category} dicts pairing equivalent goods from
    both countries, ordered as defined in _COMPARISON_PAIRS.
    """
    us_summaries = await build_good_summaries(db, country="us")
    ie_summaries = await build_good_summaries(db, country="ie")

    us_by_slug = {s.slug: s for s in us_summaries}
    ie_by_slug = {s.slug: s for s in ie_summaries}

    pairs = []
    for us_slug, ie_slug in _COMPARISON_PAIRS:
        us = us_by_slug.get(us_slug)
        ie = ie_by_slug.get(ie_slug)
        if us or ie:
            category = (us or ie).category
            pairs.append({"us": us, "ie": ie, "category": category})
    return pairs


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_closest_price(
    snapshots: list[PriceSnapshot], target_date: datetime
) -> float | None:
    """
    Find the price from the snapshot list whose collected_at is closest to
    target_date. Returns None if the list is empty.
    """
    if not snapshots:
        return None

    closest = min(snapshots, key=lambda s: abs((s.collected_at - target_date).total_seconds()))
    return closest.price_usd
