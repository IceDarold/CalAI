"""Food analysis service — orchestrates text/photo analysis through providers."""

from app.providers.base import BaseFoodTextProvider, BaseVisionProvider
from app.providers import get_text_provider, get_vision_provider
from app.schemas.food import FoodAnalysis


class FoodAnalyzer:
    """Analyzes food from text descriptions or photos."""

    def __init__(self) -> None:
        self._text_provider: BaseFoodTextProvider = get_text_provider()
        self._vision_provider: BaseVisionProvider = get_vision_provider()

    async def analyze_text(self, text: str) -> FoodAnalysis:
        """Analyze food from text message."""
        return await self._text_provider.analyze_food_text(text)

    async def analyze_photo(
        self, photo_path: str, caption: str | None = None
    ) -> FoodAnalysis:
        """Analyze food from photo, optionally using caption as context."""
        return await self._vision_provider.analyze_food_photo(photo_path, caption)

    @property
    def has_vision(self) -> bool:
        """Check if the current vision provider can actually analyze photos."""
        from app.config import settings
        return settings.vision_provider != "mock"
