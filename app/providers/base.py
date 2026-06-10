"""Abstract base classes for LLM/Vision providers."""

from abc import ABC, abstractmethod

from app.schemas.food import FoodAnalysis
from app.schemas.intent import IntentResult


class BaseFoodTextProvider(ABC):
    """Analyzes food descriptions from text."""

    @abstractmethod
    async def analyze_food_text(
        self, text: str, context: dict | None = None
    ) -> FoodAnalysis:
        ...


class BaseVisionProvider(ABC):
    """Analyzes food from photos."""

    @abstractmethod
    async def analyze_food_photo(
        self, photo_path: str, caption: str | None = None
    ) -> FoodAnalysis:
        ...


class BaseIntentProvider(ABC):
    """Detects user intent from text."""

    @abstractmethod
    async def detect_intent(self, text: str) -> IntentResult:
        ...
