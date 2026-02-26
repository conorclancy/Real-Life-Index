"""
Database engine and session setup using SQLAlchemy async with SQLite.

Usage in route handlers:
    async def my_route(db: AsyncSession = Depends(get_db)):
        result = await db.execute(...)
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# The async engine — connects to the SQLite file defined in .env
engine = create_async_engine(
    settings.DATABASE_URL,
    # Echo SQL statements to the console in development (useful for debugging)
    echo=settings.APP_ENV == "development",
    # SQLite-specific: allow the connection to be used across threads
    connect_args={"check_same_thread": False},
)

# Session factory — call this to get a new session
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,  # Keep objects usable after commit
)


async def init_db() -> None:
    """
    Create all database tables if they don't already exist.
    Called once on application startup via the lifespan handler.
    """
    # Import here to avoid circular imports — models must be registered
    # with Base before create_all is called
    from app.models import Base  # noqa: PLC0415

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields a database session per request.
    The session is automatically closed when the request finishes.

    Usage:
        async def my_route(db: AsyncSession = Depends(get_db)):
    """
    async with AsyncSessionLocal() as session:
        yield session
