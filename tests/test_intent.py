"""Tests for intent detection (rule-based fallback)."""

import pytest

from app.providers.mock import MockProvider
from app.schemas.intent import IntentType


@pytest.fixture
def intent_provider():
    return MockProvider()


@pytest.mark.asyncio
async def test_log_meal_food_description(intent_provider):
    """'съел курицу с рисом' → log_meal."""
    result = await intent_provider.detect_intent("съел курицу с рисом")
    assert result.intent == IntentType.LOG_MEAL


@pytest.mark.asyncio
async def test_log_meal_food_keywords(intent_provider):
    """'на обед гречка с котлетой' → log_meal."""
    result = await intent_provider.detect_intent("на обед гречка с котлетой")
    assert result.intent == IntentType.LOG_MEAL


@pytest.mark.asyncio
async def test_show_today(intent_provider):
    """'что я сегодня ел' → show_today."""
    result = await intent_provider.detect_intent("что я сегодня ел")
    assert result.intent == IntentType.SHOW_TODAY


@pytest.mark.asyncio
async def test_help(intent_provider):
    """'помощь' → help."""
    result = await intent_provider.detect_intent("помощь")
    assert result.intent == IntentType.HELP


@pytest.mark.asyncio
async def test_unknown(intent_provider):
    """'как дела' → unknown (не содержит food-индикаторов)."""
    result = await intent_provider.detect_intent("как дела")
    assert result.intent == IntentType.UNKNOWN


@pytest.mark.asyncio
async def test_log_meal_snack(intent_provider):
    """'перекусил йогуртом' → log_meal."""
    result = await intent_provider.detect_intent("перекусил йогуртом")
    assert result.intent == IntentType.LOG_MEAL


@pytest.mark.asyncio
async def test_greeting_is_unknown(intent_provider):
    """'привет' → unknown."""
    result = await intent_provider.detect_intent("привет")
    assert result.intent == IntentType.UNKNOWN
