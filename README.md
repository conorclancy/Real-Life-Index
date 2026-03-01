# Real Life Index

A personal side project — a self-hosted dashboard that tracks the real-world price of everyday goods over time.

I built this to answer a simple question: *are things actually getting more expensive, and by how much?* It pulls prices from official government APIs and public websites, stores them in a local SQLite database, and renders an interactive dashboard so you can see the trends for yourself.

Two baskets are tracked side by side: **United States** and **Ireland**.

---

## Features

- **Price cards** — current price, 1-year % change badge, and a sparkline mini-chart per item
- **Top Movers panel** — the 3 biggest risers and 3 biggest fallers over the past year at a glance
- **Sort controls** — re-order cards by price or % change client-side with no page reload
- **Price history chart** — interactive Plotly chart with 1M / 6M / 1Y / All range selectors
- **US vs Ireland comparison** — side-by-side table of equivalent goods with a live currency toggle (Native / All USD / All EUR), exchange rate sourced from the ECB via [frankfurter.app](https://www.frankfurter.app)
- **Stats grid** — current price, 1M / 6M / 1Y % change, all-time high and all-time low on each item's detail page
- **Auto-refresh** — APScheduler runs collectors in the background on a schedule; a manual `/admin/refresh` endpoint is also available
- **Dark theme** — because staring at a white screen at midnight is not fun

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | [FastAPI](https://fastapi.tiangolo.com/) |
| Database | SQLite via [SQLAlchemy](https://www.sqlalchemy.org/) (async) + [aiosqlite](https://github.com/omnilib/aiosqlite) |
| Scheduling | [APScheduler](https://apscheduler.readthedocs.io/) |
| HTTP client | [httpx](https://www.python-httpx.org/) |
| Templating | [Jinja2](https://jinja.palletsprojects.com/) |
| Styling | [Tailwind CSS](https://tailwindcss.com/) (CDN) |
| Charts | [Plotly.js](https://plotly.com/javascript/) |
| Python | 3.13+ |
| Package manager | [uv](https://docs.astral.sh/uv/) |

---

## Data Sources

### United States

| Item | Source | Notes |
|---|---|---|
| Eggs, Milk, Bread, Ground Beef, Chicken, Coffee, Beer | [Bureau of Labor Statistics API](https://www.bls.gov/developers/) | Monthly US city average retail prices |
| Regular Gasoline | [EIA Open Data API](https://www.eia.gov/opendata/) | Weekly US national average |
| McDonald's Big Mac Meal | Manual | Verified periodically against public listings |
| Netflix Standard Plan | Manual | Verified periodically |

### Ireland

| Item | Source | Notes |
|---|---|---|
| Eggs, Milk, Bread, Beef Mince, Chicken, Coffee, Beer | [Lidl.ie](https://www.lidl.ie) scraper | Lidl own-brand / cheapest comparable item |
| Unleaded Petrol | [AA Ireland Fuel Tracker](https://www.aaireland.ie) scraper | National average €/litre |
| Supermac's Regular Meal | Manual | Verified periodically |
| Netflix Standard Plan | Manual | Verified periodically |

### Exchange Rate (Comparison page)

Live USD/EUR rate from [frankfurter.app](https://www.frankfurter.app) (European Central Bank data). Cached in-process for 1 hour. Falls back to a hardcoded estimate if the request fails.

---

## Getting Started

### Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) (recommended) — or pip

### 1. Clone and install

```bash
git clone https://github.com/your-username/real-life-index.git
cd real-life-index
uv sync
```

### 2. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env`:

```env
# Required for the BLS API (free, no rate limit without key — key unlocks higher limits)
BLS_API_KEY=your_bls_api_key_here

# Required for the EIA API (free registration at eia.gov)
EIA_API_KEY=your_eia_api_key_here

# Protects the manual /admin/refresh endpoint
ADMIN_TOKEN=some-long-random-string
```

Both BLS and EIA keys are free to obtain — registration takes about 2 minutes on each site. The app will still work without them but may hit rate limits.

### 3. Run

```bash
uv run uvicorn app:app --reload
```

Open [http://localhost:8000](http://localhost:8000).

The database is created and seeded automatically on first startup. The first time you run it you will have no historical data — the scheduler will collect the current period's prices within a few minutes, or you can trigger it immediately:

```bash
curl -X POST http://localhost:8000/admin/refresh \
     -H "x-admin-token: your-admin-token"
```

### 4. Backfill historical data (optional but recommended)

Two scripts populate years of historical data so charts are meaningful from day one:

```bash
# US historical data (BLS / EIA backfill)
uv run python scripts/backfill_history.py

# Ireland historical data from CSO national price statistics
# Run AFTER the app has scraped at least one set of current IE prices
uv run python scripts/backfill_cso.py
```

---

## Project Structure

```
app/
├── __init__.py              # Startup: DB init → migrations → seed goods
├── models.py                # ORM models + GOODS_SEED catalogue
├── database.py              # DB initialisation and migrations
├── schemas.py               # Pydantic response schemas
├── config.py                # Settings (reads from .env)
├── api/
│   └── routes.py            # All FastAPI route handlers
├── services/
│   ├── price_service.py     # DB queries + Plotly chart builders
│   ├── refresh_service.py   # Collector orchestration
│   └── fx_service.py        # Live USD/EUR exchange rate (frankfurter.app)
├── collectors/
│   ├── bls_client.py        # BLS API (US groceries)
│   ├── eia_client.py        # EIA API (US gasoline)
│   ├── lidl_ie_client.py    # Lidl Ireland scraper
│   ├── aa_ireland_client.py # AA Ireland petrol scraper
│   └── static_prices.py    # Manually maintained prices
└── templates/
    ├── base.html            # Shared layout (nav, footer)
    ├── index.html           # Dashboard home
    ├── detail.html          # Per-item detail + history chart
    └── compare.html         # US vs Ireland side-by-side comparison

scripts/
├── backfill_history.py      # Populate US historical data (run once)
└── backfill_cso.py          # Populate IE historical data from CSO (run once)
```

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Dashboard home (`?country=us` or `?country=ie`) |
| `GET` | `/compare` | US vs Ireland comparison table |
| `GET` | `/good/{slug}` | Per-item detail page |
| `GET` | `/api/prices` | JSON — latest price for all goods |
| `GET` | `/api/prices/{slug}/history` | JSON — price history (`?months=12`) |
| `POST` | `/admin/refresh` | Trigger manual data collection (requires `x-admin-token` header) |
| `GET` | `/health` | Health check |

---

## Notes

- This is a **personal side project** — it's not production-hardened, doesn't have user accounts, and is designed to run locally or on a single small server.
- The Lidl.ie scraper is best-effort; if Lidl changes their site structure it may stop returning prices. The dashboard retains the last known price in that case.
- Prices are tracked in their **local currency** (USD for US goods, EUR for Irish goods). The comparison page converts using a live ECB exchange rate, but product units are different across countries so direct comparisons should be taken with a pinch of salt.
- Historical data coverage varies by item: BLS data goes back decades, the Irish data is backfilled from CSO statistics where available.
