"""
SQLAlchemy ORM models and the goods seed catalogue.

Two tables:
  - Good: master list of the 10 tracked everyday items
  - PriceSnapshot: one row per price observation (builds the time series)
"""

from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    """Shared base class — all models inherit from this."""
    pass


class Good(Base):
    """
    A tracked everyday good (eggs, milk, gasoline, etc.).
    Populated once at startup by seed_goods() and rarely changes.
    """
    __tablename__ = "goods"

    id        = Column(Integer, primary_key=True)
    slug      = Column(String(50), unique=True, nullable=False)   # e.g. "eggs"
    name      = Column(String(100), nullable=False)                # e.g. "Dozen Eggs"
    unit      = Column(String(50), nullable=False)                 # e.g. "per dozen"
    category  = Column(String(50), nullable=False)                 # "grocery" | "fuel" | "subscription"
    source    = Column(String(50), nullable=False)                 # "bls" | "eia" | "static"
    source_id = Column(String(100), nullable=True)                 # BLS/EIA series ID (null for static)
    emoji     = Column(String(10), nullable=False, default="🛒")   # Shown on the dashboard cards

    snapshots = relationship("PriceSnapshot", back_populates="good", lazy="selectin")

    def __repr__(self) -> str:
        return f"<Good slug={self.slug!r} source={self.source!r}>"


class PriceSnapshot(Base):
    """
    A single price observation for a good at a point in time.
    Rows accumulate over time to form the historical price series used for charts.
    """
    __tablename__ = "price_snapshots"

    id           = Column(Integer, primary_key=True)
    good_id      = Column(Integer, ForeignKey("goods.id"), nullable=False)
    price_usd    = Column(Float, nullable=False)                    # Price in US dollars
    collected_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    # BLS format: "2024-M12" | EIA format: "2024-W01" | static: "static"
    period_label = Column(String(20), nullable=True)
    source_raw   = Column(String(500), nullable=True)               # Raw value string from the API

    good = relationship("Good", back_populates="snapshots")

    __table_args__ = (
        # One snapshot per good per period — prevents duplicates on re-runs
        UniqueConstraint("good_id", "period_label", name="uq_good_period"),
        # Speeds up history queries (most common query pattern)
        Index("ix_snapshots_good_collected", "good_id", "collected_at"),
    )

    def __repr__(self) -> str:
        return f"<PriceSnapshot good_id={self.good_id} price={self.price_usd} period={self.period_label!r}>"


# ---------------------------------------------------------------------------
# Goods catalogue
# The 10 everyday items tracked by this dashboard.
# source_id is the BLS Average Retail Prices series ID or EIA series ID.
# ---------------------------------------------------------------------------
GOODS_SEED: list[dict] = [
    {
        "slug": "eggs",
        "name": "Dozen Eggs",
        "unit": "per dozen",
        "category": "grocery",
        "source": "bls",
        "source_id": "APU0000708111",  # Eggs, grade A, large, per doz. — US city avg
        "emoji": "🥚",
    },
    {
        "slug": "milk",
        "name": "Whole Milk",
        "unit": "per gallon",
        "category": "grocery",
        "source": "bls",
        "source_id": "APU0000709112",  # Milk, whole, fortified, per gal. — US city avg
        "emoji": "🥛",
    },
    {
        "slug": "bread",
        "name": "White Bread",
        "unit": "per pound",
        "category": "grocery",
        "source": "bls",
        "source_id": "APU0000702111",  # Bread, white, pan, per lb. — US city avg
        "emoji": "🍞",
    },
    {
        "slug": "ground_beef",
        "name": "Ground Beef",
        "unit": "per pound",
        "category": "grocery",
        "source": "bls",
        "source_id": "APU0000703112",  # Ground beef, 100% beef, per lb. — US city avg
        "emoji": "🥩",
    },
    {
        "slug": "chicken",
        "name": "Chicken (whole)",
        "unit": "per pound",
        "category": "grocery",
        "source": "bls",
        "source_id": "APU0000706111",  # Chicken, fresh, whole, per lb. — US city avg
        "emoji": "🍗",
    },
    {
        "slug": "coffee",
        "name": "Ground Coffee",
        "unit": "per pound",
        "category": "grocery",
        "source": "bls",
        "source_id": "APU0000717311",  # Coffee, 100%, ground roast, all sizes, per lb.
        "emoji": "☕",
    },
    {
        "slug": "beer",
        "name": "Beer (6-pack)",
        "unit": "per 6-pack",
        "category": "grocery",
        "source": "bls",
        "source_id": "APU0000720311",  # Beer, away from home, per 16 oz. — US city avg
        "emoji": "🍺",
    },
    {
        "slug": "gasoline",
        "name": "Regular Gasoline",
        "unit": "per gallon",
        "category": "fuel",
        "source": "eia",
        "source_id": "EMM_EPM0_PTE_NUS_DPG",  # US regular conventional gas, $/gallon
        "emoji": "⛽",
    },
    {
        "slug": "mcdonalds",
        "name": "McDonald's Big Mac Meal",
        "unit": "per meal (medium)",
        "category": "dining",
        "source": "static",
        "source_id": None,
        "emoji": "🍔",
    },
    {
        "slug": "netflix",
        "name": "Netflix Standard Plan",
        "unit": "per month",
        "category": "subscription",
        "source": "static",
        "source_id": None,
        "emoji": "📺",
    },
]
