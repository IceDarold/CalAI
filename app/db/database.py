"""Async SQLAlchemy engine and session factory."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db.models import Base

engine = create_async_engine(settings.database_url, echo=False)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


_MIGRATIONS = [
    # Add fat/carbs to meals
    "ALTER TABLE meals ADD COLUMN fat_min_g REAL",
    "ALTER TABLE meals ADD COLUMN fat_max_g REAL",
    "ALTER TABLE meals ADD COLUMN carbs_min_g REAL",
    "ALTER TABLE meals ADD COLUMN carbs_max_g REAL",
    # Add fat/carbs to meal_items
    "ALTER TABLE meal_items ADD COLUMN fat_min_g REAL",
    "ALTER TABLE meal_items ADD COLUMN fat_max_g REAL",
    "ALTER TABLE meal_items ADD COLUMN carbs_min_g REAL",
    "ALTER TABLE meal_items ADD COLUMN carbs_max_g REAL",
    # Add profile fields to users
    "ALTER TABLE users ADD COLUMN height_cm REAL",
    "ALTER TABLE users ADD COLUMN weight_kg REAL",
    "ALTER TABLE users ADD COLUMN age INTEGER",
    "ALTER TABLE users ADD COLUMN gender VARCHAR(10)",
    "ALTER TABLE users ADD COLUMN goal VARCHAR(20)",
    "ALTER TABLE users ADD COLUMN target_kcal INTEGER",
    "ALTER TABLE users ADD COLUMN target_protein_g INTEGER",
    "ALTER TABLE users ADD COLUMN target_fat_g INTEGER",
    "ALTER TABLE users ADD COLUMN target_carbs_g INTEGER",
    # last_meal_id for "и ещё" chaining (survives restart)
    "ALTER TABLE users ADD COLUMN last_meal_id INTEGER",
]


async def init_db() -> None:
    """Create tables and run migrations."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Run any pending migrations (ignore errors for already-existing columns)
        for sql in _MIGRATIONS:
            try:
                await conn.execute(text(sql))
            except Exception:
                pass  # column already exists


async def get_session() -> AsyncSession:
    """Get a new async session."""
    return async_session_factory()
