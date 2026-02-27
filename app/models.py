"""
SQLAlchemy ORM models and the goods seed catalogue.

Two tables:
  - Good: master list of tracked everyday items
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
    source    = Column(String(50), nullable=False)                 # "bls" | "eia" | "static" | "lidl_ie" | "aa_ie"
    source_id = Column(String(100), nullable=True)                 # BLS/EIA series ID or scraper search term
    emoji     = Column(String(10), nullable=False, default="🛒")   # Shown on the dashboard cards
    country   = Column(String(10), nullable=False, default="us")   # "us" | "ie"
    currency  = Column(String(5), nullable=False, default="USD")   # "USD" | "EUR"

    snapshots = relationship("PriceSnapshot", back_populates="good", lazy="selectin")

    def __repr__(self) -> str:
        return f"<Good slug={self.slug!r} source={self.source!r} country={self.country!r}>"


class PriceSnapshot(Base):
    """
    A single price observation for a good at a point in time.
    Rows accumulate over time to form the historical price series used for charts.
    """
    __tablename__ = "price_snapshots"

    id           = Column(Integer, primary_key=True)
    good_id      = Column(Integer, ForeignKey("goods.id"), nullable=False)
    price_usd    = Column(Float, nullable=False)                    # Price in local currency (USD or EUR)
    collected_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    period_label = Column(String(20), nullable=True)
    source_raw   = Column(String(500), nullable=True)               # Raw value string from the source

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
# ---------------------------------------------------------------------------
GOODS_SEED: list[dict] = [
    # =========================================================================
    # UNITED STATES BASKET
    # =========================================================================

    # GROCERIES — 7 items via BLS Average Retail Prices API
    {
        "slug": "eggs",
        "name": "Dozen Eggs",
        "unit": "per dozen",
        "category": "grocery",
        "source": "bls",
        "source_id": "APU0000708111",  # Eggs, grade A, large, per doz. — US city avg
        "emoji": "🥚",
        "country": "us",
        "currency": "USD",
    },
    {
        "slug": "milk",
        "name": "Whole Milk",
        "unit": "per gallon",
        "category": "grocery",
        "source": "bls",
        "source_id": "APU0000709112",  # Milk, whole, fortified, per gal. — US city avg
        "emoji": "🥛",
        "country": "us",
        "currency": "USD",
    },
    {
        "slug": "bread",
        "name": "White Bread",
        "unit": "per pound",
        "category": "grocery",
        "source": "bls",
        "source_id": "APU0000702111",  # Bread, white, pan, per lb. — US city avg
        "emoji": "🍞",
        "country": "us",
        "currency": "USD",
    },
    {
        "slug": "ground_beef",
        "name": "Ground Beef",
        "unit": "per pound",
        "category": "grocery",
        "source": "bls",
        "source_id": "APU0000703112",  # Ground beef, 100% beef, per lb. — US city avg
        "emoji": "🥩",
        "country": "us",
        "currency": "USD",
    },
    {
        "slug": "chicken",
        "name": "Chicken (whole)",
        "unit": "per pound",
        "category": "grocery",
        "source": "bls",
        "source_id": "APU0000706111",  # Chicken, fresh, whole, per lb. — US city avg
        "emoji": "🍗",
        "country": "us",
        "currency": "USD",
    },
    {
        "slug": "coffee",
        "name": "Ground Coffee",
        "unit": "per pound",
        "category": "grocery",
        "source": "bls",
        "source_id": "APU0000717311",  # Coffee, 100%, ground roast, all sizes, per lb.
        "emoji": "☕",
        "country": "us",
        "currency": "USD",
    },
    {
        "slug": "beer",
        "name": "Beer (6-pack)",
        "unit": "per 6-pack",
        "category": "grocery",
        "source": "bls",
        "source_id": "APU0000720311",  # Beer, away from home, per 16 oz. — US city avg
        "emoji": "🍺",
        "country": "us",
        "currency": "USD",
    },
    # FUEL — via EIA Open Data
    {
        "slug": "gasoline",
        "name": "Regular Gasoline",
        "unit": "per gallon",
        "category": "fuel",
        "source": "eia",
        "source_id": "EMM_EPM0_PTE_NUS_DPG",  # US regular conventional gas, $/gallon
        "emoji": "⛽",
        "country": "us",
        "currency": "USD",
    },
    # DINING & SUBSCRIPTIONS — manually maintained
    {
        "slug": "mcdonalds",
        "name": "McDonald's Big Mac Meal",
        "unit": "per meal (medium)",
        "category": "dining",
        "source": "static",
        "source_id": None,
        "emoji": "🍔",
        "country": "us",
        "currency": "USD",
    },
    {
        "slug": "netflix",
        "name": "Netflix Standard Plan",
        "unit": "per month",
        "category": "subscription",
        "source": "static",
        "source_id": None,
        "emoji": "📺",
        "country": "us",
        "currency": "USD",
    },

    # =========================================================================
    # IRELAND BASKET
    # Grocery prices scraped from Lidl.ie (own-brand staples).
    # Fuel prices from AA Ireland fuel tracker.
    # Static prices verified manually.
    # =========================================================================

    # GROCERIES — scraped from Lidl Ireland
    {
        "slug": "ie_eggs",
        "name": "Free Range Eggs (12)",
        "unit": "per dozen",
        "category": "grocery",
        "source": "lidl_ie",
        "source_id": "eggs",  # Search term used on lidl.ie
        "emoji": "🥚",
        "country": "ie",
        "currency": "EUR",
    },
    {
        "slug": "ie_milk",
        "name": "Fresh Milk (2L)",
        "unit": "per 2 litres",
        "category": "grocery",
        "source": "lidl_ie",
        "source_id": "milk 2l",
        "emoji": "🥛",
        "country": "ie",
        "currency": "EUR",
    },
    {
        "slug": "ie_bread",
        "name": "White Sliced Pan",
        "unit": "per loaf",
        "category": "grocery",
        "source": "lidl_ie",
        "source_id": "white sliced pan",
        "emoji": "🍞",
        "country": "ie",
        "currency": "EUR",
    },
    {
        "slug": "ie_mince",
        "name": "Lean Beef Mince",
        "unit": "per pack",
        "category": "grocery",
        "source": "lidl_ie",
        "source_id": "lean beef mince",
        "emoji": "🥩",
        "country": "ie",
        "currency": "EUR",
    },
    {
        "slug": "ie_chicken",
        "name": "Chicken Breast Fillets",
        "unit": "per pack",
        "category": "grocery",
        "source": "lidl_ie",
        "source_id": "chicken fillets",
        "emoji": "🍗",
        "country": "ie",
        "currency": "EUR",
    },
    {
        "slug": "ie_coffee",
        "name": "Ground Coffee (Bellarom)",
        "unit": "per pack",
        "category": "grocery",
        "source": "lidl_ie",
        "source_id": "bellarom",
        "emoji": "☕",
        "country": "ie",
        "currency": "EUR",
    },
    {
        "slug": "ie_beer",
        "name": "Beer Multipack",
        "unit": "per multipack",
        "category": "grocery",
        "source": "lidl_ie",
        "source_id": "beer multipack",
        "emoji": "🍺",
        "country": "ie",
        "currency": "EUR",
    },
    # FUEL — scraped from AA Ireland fuel tracker
    {
        "slug": "ie_petrol",
        "name": "Unleaded Petrol",
        "unit": "per litre",
        "category": "fuel",
        "source": "aa_ie",
        "source_id": "petrol",
        "emoji": "⛽",
        "country": "ie",
        "currency": "EUR",
    },
    # DINING & SUBSCRIPTIONS — manually maintained
    {
        "slug": "ie_supermacs",
        "name": "Supermac's Regular Meal",
        "unit": "per meal",
        "category": "dining",
        "source": "static",
        "source_id": None,
        "emoji": "🍔",
        "country": "ie",
        "currency": "EUR",
    },
    {
        "slug": "ie_netflix",
        "name": "Netflix Standard Plan",
        "unit": "per month",
        "category": "subscription",
        "source": "static",
        "source_id": None,
        "emoji": "📺",
        "country": "ie",
        "currency": "EUR",
    },
]
