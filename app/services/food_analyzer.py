"""Food analysis service — LLM parsing + USDA lookup + deterministic calculator.

Pipeline:
1. LLM parses text → food names + grams (no calorie guessing)
2. USDA food DB lookup for each parsed item
3. Deterministic calculator: kcal = food.kcal_per_100g × grams / 100
"""

from app.providers.base import BaseFoodTextProvider, BaseVisionProvider
from app.providers import get_text_provider, get_vision_provider
from app.schemas.food import (
    Confidence,
    FoodAnalysis,
    FoodItem,
    MealType,
    ParsedFoodItem,
)
from app.services.calculator import calculate_from_parsed
from app.services.food_db import search_food


class FoodAnalyzer:
    """Analyzes food from text descriptions or photos.

    Uses LLM for parsing (name + grams extraction), then USDA database
    for deterministic nutrition calculation.
    """

    def __init__(self):
        self._text_provider: BaseFoodTextProvider = get_text_provider()
        self._vision_provider: BaseVisionProvider = get_vision_provider()

    async def analyze_text(self, text: str, session=None) -> FoodAnalysis:
        """Analyze food from text message.

        Full pipeline: LLM parse → USDA lookup → calculator → enriched result.
        """
        # Step 1: LLM parses the message — extracts food names + grams
        analysis = await self._text_provider.analyze_food_text(text)

        if not analysis.is_food:
            return analysis

        # Step 2: If we have a DB session, do USDA lookup + calculation
        if session is not None and analysis.parsed_items:
            analysis = await self._enrich_with_usda(session, analysis)

        return analysis

    async def analyze_photo(
        self, photo_path: str, caption: str | None = None
    ) -> FoodAnalysis:
        """Analyze food from photo."""
        return await self._vision_provider.analyze_food_photo(photo_path, caption)

    async def _enrich_with_usda(self, session, analysis: FoodAnalysis) -> FoodAnalysis:
        """Look up parsed items in USDA DB and calculate nutrition."""
        parsed_dicts = [pi.model_dump() for pi in analysis.parsed_items]
        food_matches = []

        for pi in analysis.parsed_items:
            matches = await search_food(session, pi.name, limit=1)
            food_matches.append(matches[0] if matches else None)

        # Calculate
        calc_result = calculate_from_parsed(parsed_dicts, food_matches)

        # Populate FoodAnalysis with calculated values
        analysis.total_calories_min = int(calc_result.total_kcal * 0.85)
        analysis.total_calories_max = int(calc_result.total_kcal * 1.15)
        analysis.total_protein_min_g = round(calc_result.total_protein_g * 0.85, 1)
        analysis.total_protein_max_g = round(calc_result.total_protein_g * 1.15, 1)
        analysis.confidence = Confidence(calc_result.confidence)

        # Update items with USDA data
        enriched_items = []
        for parsed, calc_item in zip(analysis.parsed_items, calc_result.items):
            usda_name = calc_item.matched_food["name"] if calc_item.matched_food else parsed.name
            enriched_items.append(FoodItem(
                name=usda_name,
                portion_text=parsed.portion_text or f"~{calc_item.grams:.0f} г",
                calories_min=int(calc_item.kcal * 0.85),
                calories_max=int(calc_item.kcal * 1.15),
                protein_min_g=round(calc_item.protein_g * 0.85, 1),
                protein_max_g=round(calc_item.protein_g * 1.15, 1),
                confidence=Confidence(calc_item.confidence),
            ))
        analysis.items = enriched_items

        # Add calculator questions to LLM questions
        if calc_result.questions:
            analysis.questions.extend(calc_result.questions)

        return analysis

    @property
    def has_vision(self) -> bool:
        """Check if the current vision provider can actually analyze photos."""
        from app.config import settings
        return settings.ai_provider == "openai" and bool(settings.openai_api_key)
