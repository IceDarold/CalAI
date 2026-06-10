"""Pydantic schemas for intent detection."""

from enum import Enum

from pydantic import BaseModel


class IntentType(str, Enum):
    START = "start"
    HELP = "help"
    SHOW_TODAY = "show_today"
    LOG_MEAL = "log_meal"
    UNKNOWN = "unknown"


class IntentResult(BaseModel):
    """Result of intent detection."""
    intent: IntentType
    confidence: float = 0.5  # 0.0 to 1.0
    reasoning: str = ""
