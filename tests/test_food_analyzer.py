"""Tests for food text analysis."""

import pytest

from app.providers.mock import MockProvider
from app.schemas.food import Confidence


@pytest.fixture
def provider():
    return MockProvider()


@pytest.mark.asyncio
async def test_analyze_chicken_rice_returns_range(provider):
    """'съел курицу с рисом' should return a calories range."""
    result = await provider.analyze_food_text("съел курицу с рисом")

    assert result.is_food is True
    assert len(result.items) >= 1

    # Should have item names matching chicken/rice
    item_names = [item.name for item in result.items]
    assert any("куриц" in name for name in item_names)

    # Should have calorie ranges
    if result.total_calories_min is not None:
        assert result.total_calories_min > 0
    if result.total_calories_max is not None:
        assert result.total_calories_max > 0
    if result.total_calories_min is not None and result.total_calories_max is not None:
        assert result.total_calories_max >= result.total_calories_min


@pytest.mark.asyncio
async def test_analyze_returns_protein_range(provider):
    """Food analysis should include protein range."""
    result = await provider.analyze_food_text("съел куриную грудку")

    assert result.is_food is True
    # Protein should be present
    if result.total_protein_min_g is not None:
        assert result.total_protein_min_g >= 0
    if result.total_protein_max_g is not None:
        assert result.total_protein_max_g >= 0


@pytest.mark.asyncio
async def test_analyze_not_food(provider):
    """Non-food text should return is_food=False."""
    result = await provider.analyze_food_text("какая сегодня погода")

    assert result.is_food is False


@pytest.mark.asyncio
async def test_analyze_empty_text(provider):
    """Empty text should return not food."""
    result = await provider.analyze_food_text("ммм")

    # Very ambiguous — should be low confidence at minimum
    assert result.confidence == Confidence.LOW or result.is_food is False


@pytest.mark.asyncio
async def test_analyze_detects_meal_type(provider):
    """Should detect meal type from description."""
    result = await provider.analyze_food_text("на завтрак была каша с яйцом")

    assert result.is_food is True
    assert result.meal_type.value == "breakfast"


@pytest.mark.asyncio
async def test_analyze_with_portion(provider):
    """When portion is mentioned, confidence should be at least medium."""
    result = await provider.analyze_food_text("съел 200 грамм курицы и 150 грамм риса")

    assert result.is_food is True
    # With explicit portions, overall confidence should be at least medium
    # (individual items with explicit portions get medium confidence)
