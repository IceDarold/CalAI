"""Test fixtures for CalAI."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base
from app.schemas.food import Confidence, FoodAnalysis, FoodItem, MealType

# Use in-memory SQLite for tests
TEST_DB_URL = "sqlite+aiosqlite://"


@pytest.fixture
async def engine():
    """Create a test database engine."""
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def session(engine):
    """Create a test database session."""
    async_session = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with async_session() as session:
        yield session
        await session.rollback()


@pytest.fixture
def sample_food_analysis() -> FoodAnalysis:
    """A sample food analysis result."""
    return FoodAnalysis(
        is_food=True,
        meal_type=MealType.LUNCH,
        items=[
            FoodItem(
                name="курица",
                portion_text="~150 г",
                calories_min=200,
                calories_max=300,
                protein_min_g=35,
                protein_max_g=50,
                confidence=Confidence.MEDIUM,
            ),
            FoodItem(
                name="рис",
                portion_text="~150 г",
                calories_min=150,
                calories_max=200,
                protein_min_g=3,
                protein_max_g=5,
                confidence=Confidence.MEDIUM,
            ),
        ],
        total_calories_min=350,
        total_calories_max=500,
        total_protein_min_g=38.0,
        total_protein_max_g=55.0,
        confidence=Confidence.MEDIUM,
        questions=[],
    )
