"""Tests for meal logging."""

import pytest
from sqlalchemy import select

from app.db.models import Meal
from app.db.repositories import get_or_create_user, save_meal, get_today_meals
from app.services.meal_logger import MealLogger
from app.services.summary import SummaryService
from app.schemas.food import Confidence, FoodAnalysis, FoodItem, MealType


@pytest.mark.asyncio
async def test_save_meal_in_db(session, sample_food_analysis):
    """A meal should be saved to the database."""
    # Create user
    user = await get_or_create_user(session, telegram_id=12345, first_name="Test")
    await session.flush()

    meal = await save_meal(
        session,
        user_id=user.id,
        meal_type="lunch",
        source_type="text",
        original_text="съел курицу с рисом",
        photo_path=None,
        calories_min=sample_food_analysis.total_calories_min,
        calories_max=sample_food_analysis.total_calories_max,
        protein_min_g=sample_food_analysis.total_protein_min_g,
        protein_max_g=sample_food_analysis.total_protein_max_g,
        confidence="medium",
        status="confirmed",
        items_data=[
            {
                "name": "курица",
                "portion_text": "~150 г",
                "calories_min": 200,
                "calories_max": 300,
                "protein_min_g": 35.0,
                "protein_max_g": 50.0,
                "confidence": "medium",
            },
        ],
    )

    assert meal.id is not None
    assert meal.meal_type == "lunch"
    assert len(meal.items) == 1
    assert meal.items[0].name == "курица"


@pytest.mark.asyncio
async def test_get_today_meals(session, sample_food_analysis):
    """Today's meals should be retrievable."""
    user = await get_or_create_user(session, telegram_id=12346, first_name="Test")
    await session.flush()

    # Save a meal
    await save_meal(
        session,
        user_id=user.id,
        meal_type="lunch",
        source_type="text",
        original_text="съел курицу с рисом",
        photo_path=None,
        calories_min=sample_food_analysis.total_calories_min,
        calories_max=sample_food_analysis.total_calories_max,
        protein_min_g=sample_food_analysis.total_protein_min_g,
        protein_max_g=sample_food_analysis.total_protein_max_g,
        confidence="medium",
        status="confirmed",
        items_data=[],
    )

    meals = await get_today_meals(session, user.id)
    assert len(meals) == 1


@pytest.mark.asyncio
async def test_meal_logger_from_text(session, sample_food_analysis):
    """MealLogger should create a meal and return a response."""
    logger = MealLogger(session)

    user_id = await logger.ensure_user(telegram_id=12347, username="testuser", first_name="Test")
    meal, response = await logger.log_from_text(user_id, "съел курицу с рисом", sample_food_analysis)

    assert meal is not None
    assert meal.id is not None
    assert meal.status == "confirmed"
    assert "Записал" in response
    assert "ккал" in response


@pytest.mark.asyncio
async def test_meal_logger_draft_on_low_confidence(session):
    """Low confidence analysis should result in draft status."""
    logger = MealLogger(session)
    user_id = await logger.ensure_user(telegram_id=12348, username=None, first_name="Test")

    low_conf_analysis = FoodAnalysis(
        is_food=True,
        meal_type=MealType.UNKNOWN,
        items=[
            FoodItem(
                name="приём пищи",
                portion_text="неизвестно",
                calories_min=300,
                calories_max=700,
                protein_min_g=15.0,
                protein_max_g=40.0,
                confidence=Confidence.LOW,
            ),
        ],
        total_calories_min=300,
        total_calories_max=700,
        total_protein_min_g=15.0,
        total_protein_max_g=40.0,
        confidence=Confidence.LOW,
        questions=["Сколько порций было?"],
    )

    meal, response = await logger.log_from_text(user_id, "что-то съел", low_conf_analysis)

    assert meal.status == "draft"
    assert "низкая" in response.lower() or "Уверенность низкая" in response


@pytest.mark.asyncio
async def test_summary_empty(session):
    """Summary for a user with no meals should say nothing recorded."""
    user = await get_or_create_user(session, telegram_id=12349, first_name="Test")
    await session.flush()

    summary_service = SummaryService(session)
    result = await summary_service.get_today_summary(user.id)

    assert "не записал" in result.lower() or "ничего" in result.lower()


@pytest.mark.asyncio
async def test_summary_with_meals(session, sample_food_analysis):
    """Summary should include meal details and totals."""
    user = await get_or_create_user(session, telegram_id=12350, first_name="Test")
    await session.flush()

    # Save a confirmed meal
    await save_meal(
        session,
        user_id=user.id,
        meal_type="lunch",
        source_type="text",
        original_text="съел курицу с рисом",
        photo_path=None,
        calories_min=sample_food_analysis.total_calories_min,
        calories_max=sample_food_analysis.total_calories_max,
        protein_min_g=sample_food_analysis.total_protein_min_g,
        protein_max_g=sample_food_analysis.total_protein_max_g,
        confidence="medium",
        status="confirmed",
        items_data=[
            {"name": item.name, "portion_text": item.portion_text,
             "calories_min": item.calories_min, "calories_max": item.calories_max,
             "protein_min_g": item.protein_min_g, "protein_max_g": item.protein_max_g,
             "confidence": item.confidence.value}
            for item in sample_food_analysis.items
        ],
    )

    summary_service = SummaryService(session)
    result = await summary_service.get_today_summary(user.id)

    assert "курица" in result.lower()
    assert "ккал" in result.lower()
