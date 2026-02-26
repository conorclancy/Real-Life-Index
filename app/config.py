"""
Configuration settings loaded from the .env file.

To set up, create a .env file in the project root with:
    BLS_API_KEY=your_key_here
    EIA_API_KEY=optional_key_here
    ADMIN_TOKEN=some_random_string
    DATABASE_URL=sqlite+aiosqlite:///./prices.db

Get a free BLS API key at: https://data.bls.gov/registrationEngine/
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # Don't crash if .env is missing — useful during initial setup
        env_ignore_empty=True,
    )

    # BLS API — required for grocery price data
    # Register free at: https://data.bls.gov/registrationEngine/
    BLS_API_KEY: str = ""

    # EIA API — optional, improves rate limits for gasoline data
    # Register free at: https://www.eia.gov/opendata/register.php
    EIA_API_KEY: str = ""

    # Protects the /admin/refresh endpoint — change this to something random
    ADMIN_TOKEN: str = "change-me-in-production"

    # SQLite database file path (async driver)
    DATABASE_URL: str = "sqlite+aiosqlite:///./prices.db"

    # App environment — set to "production" when deploying
    APP_ENV: str = "development"


# Single shared instance — import this throughout the app
settings = Settings()
