"""Tests for intent detection."""

import pytest

from app.providers.mock import MockProvider
from app.schemas.intent import IntentType


@pytest.fixture
def intent_provider():
    return MockProvider()


@pytest.mark.asyncio
async def test_log_meal_food_description(intent_provider):
    """Text 'съел курицу с рисом' should be detected as log_meal."""
    result = await intent_provider.detect_intent("съел курицу с рисом")
    assert result.intent == IntentType.LOG_MEAL


@pytest.mark.asyncio
async def test_log_meal_food_keywords(intent_provider):
    """Messages with food keywords should be log_meal."""
    result = await intent_provider.detect_intent("на обед гречка с котлетой")
    assert result.intent == IntentType.LOG_MEAL


@pytest.mark.asyncio
async def test_show_today(intent_provider):
    """'что я сегодня ел' should be show_today."""
    result = await intent_provider.detect_intent("что я сегодня ел")
    assert result.intent == IntentType.SHOW_TODAY


@pytest.mark.asyncio
async def test_show_today_alt(intent_provider):
    """'сегодня' should be show_today."""
    result = await intent_provider.detect_intent("сегодня")
    assert result.intent == IntentType.SHOW_TODAY


@pytest.mark.asyncio
async def test_help(intent_provider):
    """'помощь' should be help."""
    result = await intent_provider.detect_intent("помощь")
    assert result.intent == IntentType.HELP


@pytest.mark.asyncio
async def test_unknown(intent_provider):
    """Irrelevant text should be unknown."""
    result = await intent_provider.detect_intent("привет как дела")
    assert result.intent == IntentType.UNKNOWN


@pytest.mark.asyncio
async def test_log_meal_snack(intent_provider):
    """Snack description should be log_meal."""
    result = await intent_provider.detect_intent("перекусил йогуртом")
    assert result.intent == IntentType.LOG_MEAL
