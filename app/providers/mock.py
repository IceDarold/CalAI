"""Mock provider — rule-based food analysis without any LLM.

Uses a small built-in food database and simple keyword matching.
Returns conservative ranges and low confidence for ambiguous input.
"""

import re
import json
from typing import ClassVar

from app.providers.base import (
    BaseFoodTextProvider,
    BaseIntentProvider,
    BaseVisionProvider,
)
from app.schemas.food import (
    Confidence,
    FoodAnalysis,
    FoodItem,
    MealType,
)
from app.schemas.intent import IntentResult, IntentType


# ── Food database ──────────────────────────────────────────────────────────
# Each entry: (keywords, name, kcal_per_100g, protein_per_100g, typical_portion_g)
FOOD_DB: list[tuple[list[str], str, int, float, int]] = [
    (["курин", "куриц", "курица", "куриное", "куриный", "грудк", "филе", "chicken", "цыпленок"], "курица", 165, 31.0, 150),
    (["рис", "rice"], "рис", 130, 2.7, 150),
    (["гречк", "греча", "buckwheat"], "гречка", 110, 4.0, 150),
    (["картошк", "картофел", "potato", "пюре"], "картофель", 80, 2.0, 200),
    (["макарон", "паст", "спагетти", "pasta"], "макароны", 130, 5.0, 150),
    (["хлеб", "bread", "булк", "батон", "тост"], "хлеб", 250, 8.0, 50),
    (["яйц", "яичниц", "омлет", "egg"], "яйца", 155, 13.0, 100),
    (["котлет", "cutlet"], "котлета", 250, 18.0, 120),
    (["сосиск", "колбас", "sausage"], "сосиски/колбаса", 280, 12.0, 80),
    (["сыр", "cheese", "пармезан", "моцарелла", "сулугуни"], "сыр", 350, 25.0, 40),
    (["творог", "cottage cheese"], "творог", 110, 16.0, 150),
    (["йогурт", "yogurt", "кефир"], "йогурт", 85, 8.0, 200),
    (["говядин", "мясо", "стейк", "beef", "steak", "телятин"], "говядина", 250, 26.0, 150),
    (["свинин", "pork", "шашлык"], "свинина", 290, 25.0, 150),
    (["рыб", "fish", "лосос", "salmon", "тунец", "треск", "форел"], "рыба", 180, 20.0, 150),
    (["овощ", "огурец", "помидор", "томат", "перец", "салат", "зелен", "vegetable", "овощной"], "овощи", 25, 1.5, 150),
    (["фрукт", "яблок", "банан", "апельсин", "fruit", "груш"], "фрукты", 60, 0.5, 150),
    (["суп", "борщ", "soup", "бульон", "щи", "солянк", "ух"], "суп", 70, 5.0, 300),
    (["каш", "овсянк", "oatmeal", "porridge", "манн", "пшен"], "каша", 100, 3.5, 200),
    (["шоколад", "конфет", "сладк", "печень", "торт", "десерт", "chocolate", "candy"], "сладости", 450, 5.0, 50),
    (["орех", "миндаль", "кешью", "арахис", "nut", "семечк"], "орехи", 600, 20.0, 30),
    (["салат", "цезарь", "греческий", "овощной салат"], "салат", 80, 4.0, 200),
]


# Meal time keywords for guessing meal_type
MEAL_TYPE_KEYWORDS: dict[str, list[str]] = {
    MealType.BREAKFAST.value: ["завтрак", "утром", "утро", "каш", "яичниц", "омлет", "йогурт", "мюсли"],
    MealType.LUNCH.value: ["обед", "днем", "днём", "ланч", "lunch"],
    MealType.DINNER.value: ["ужин", "вечером", "вечер", "dinner"],
    MealType.SNACK.value: ["перекус", "снэк", "snack", "фрукт", "орех", "йогурт", "банан", "яблок"],
}


class MockProvider(BaseFoodTextProvider, BaseIntentProvider, BaseVisionProvider):
    """Mock provider that uses rule-based matching.

    Combines all three interfaces for simplicity.
    In a real setup, the mock is replaced by actual LLM providers.
    """

    async def detect_intent(self, text: str) -> IntentResult:
        """Rule-based intent detection."""
        text_lower = text.lower().strip()

        # Check for /today patterns
        today_patterns = ["что я сегодня ел", "итоги", "сегодня", "что сегодня", "мой рацион"]
        for p in today_patterns:
            if p in text_lower:
                return IntentResult(intent=IntentType.SHOW_TODAY, confidence=0.9, reasoning=f"matched pattern: {p}")

        # Check for help patterns
        help_patterns = ["помощь", "help", "что ты умеешь", "как использовать", "команды"]
        for p in help_patterns:
            if p in text_lower:
                return IntentResult(intent=IntentType.HELP, confidence=0.9, reasoning=f"matched pattern: {p}")

        # Check for food-related content — use word boundaries to avoid false positives
        # e.g. "дела" should NOT match "ел"
        food_indicators = ["съел", "ел", "ем", "поел", "поела", "кушал", "пообедал",
                          "позавтракал", "поужинал", "перекусил", "завтрак", "обед",
                          "ужин", "перекус", "приготовил", "заказал", "сохрани"]

        for ind in food_indicators:
            if re.search(r'\b' + re.escape(ind) + r'\b', text_lower):
                return IntentResult(intent=IntentType.LOG_MEAL, confidence=0.7, reasoning=f"food indicator: {ind}")

        food_words = ["куриц", "рис", "гречк", "мяс", "рыб", "суп", "салат",
                     "каш", "йогурт", "хлеб", "яйц", "овощ", "фрукт", "котлет",
                     "картошк", "макарон", "сыр", "творог", "сосиск"]

        for fw in food_words:
            if fw in text_lower:
                return IntentResult(intent=IntentType.LOG_MEAL, confidence=0.7, reasoning=f"food word: {fw}")

        return IntentResult(intent=IntentType.UNKNOWN, confidence=0.3, reasoning="no patterns matched")

    async def analyze_food_text(
        self, text: str, context: dict | None = None
    ) -> FoodAnalysis:
        """Analyze food from text using keyword matching and food DB."""
        text_lower = text.lower()

        # Detect meal type
        meal_type = self._detect_meal_type(text_lower)

        # Find matching food items
        matched_items: list[FoodItem] = []
        seen_names: set[str] = set()

        for keywords, name, kcal_per_100g, protein_per_100g, typical_portion in FOOD_DB:
            for kw in keywords:
                if kw.lower() in text_lower and name not in seen_names:
                    portion = typical_portion
                    cal = int(kcal_per_100g * portion / 100)
                    prot = round(protein_per_100g * portion / 100, 1)

                    # Add some range
                    cal_min = int(cal * 0.8)
                    cal_max = int(cal * 1.2)
                    prot_min = round(prot * 0.8, 1)
                    prot_max = round(prot * 1.2, 1)

                    # Determine confidence: if portion is explicitly mentioned, medium; else low
                    conf = Confidence.LOW
                    if re.search(r'(\d+)\s*(г|грамм|кг|порци|тарелк|кусок|штук|шт)', text_lower):
                        conf = Confidence.MEDIUM

                    matched_items.append(FoodItem(
                        name=name,
                        portion_text=f"~{portion} г",
                        calories_min=cal_min,
                        calories_max=cal_max,
                        protein_min_g=prot_min,
                        protein_max_g=prot_max,
                        confidence=conf,
                    ))
                    seen_names.add(name)
                    break

        # If no items matched, try to extract any food-like words as generic items
        if not matched_items:
            # Look for generic food mentions
            generic_items = self._extract_generic_items(text_lower)
            if generic_items:
                matched_items = generic_items
                confidence = Confidence.LOW
            else:
                return FoodAnalysis(
                    is_food=False,
                    meal_type=meal_type,
                    confidence=Confidence.LOW,
                    questions=["Что именно ты съел? Можешь описать продукты или блюда."],
                )
        else:
            confidence = self._overall_confidence(matched_items)

        # Calculate totals
        total_cal_min = sum(item.calories_min or 0 for item in matched_items)
        total_cal_max = sum(item.calories_max or 0 for item in matched_items)
        total_prot_min = round(sum(item.protein_min_g or 0 for item in matched_items), 1)
        total_prot_max = round(sum(item.protein_max_g or 0 for item in matched_items), 1)

        # Generate questions if confidence is low
        questions: list[str] = []
        if confidence == Confidence.LOW:
            portion_questions = self._generate_portion_questions(matched_items)
            questions.extend(portion_questions)

        return FoodAnalysis(
            is_food=True,
            meal_type=meal_type,
            items=matched_items,
            total_calories_min=total_cal_min if total_cal_min > 0 else None,
            total_calories_max=total_cal_max if total_cal_max > 0 else None,
            total_protein_min_g=total_prot_min if total_prot_min > 0 else None,
            total_protein_max_g=total_prot_max if total_prot_max > 0 else None,
            confidence=confidence,
            questions=questions,
        )

    async def analyze_food_photo(
        self, photo_path: str, caption: str | None = None
    ) -> FoodAnalysis:
        """Mock vision — cannot actually analyze photos.

        Returns a result indicating vision is not available.
        Uses the caption if provided for text-based analysis.
        """
        if caption and caption.strip():
            # If there's a caption, treat it like text analysis
            return await self.analyze_food_text(caption)
        return FoodAnalysis(
            is_food=False,
            meal_type=MealType.UNKNOWN,
            confidence=Confidence.LOW,
            questions=["Не могу анализировать фото без vision-модели. Опиши, пожалуйста, что было на фото."],
        )

    def _detect_meal_type(self, text: str) -> MealType:
        """Guess meal type from text keywords."""
        for meal_type, keywords in MEAL_TYPE_KEYWORDS.items():
            for kw in keywords:
                if kw in text:
                    return MealType(meal_type)
        return MealType.UNKNOWN

    def _overall_confidence(self, items: list[FoodItem]) -> Confidence:
        """Determine overall confidence from individual items."""
        if not items:
            return Confidence.LOW
        confidences = [item.confidence for item in items]
        if all(c == Confidence.HIGH for c in confidences):
            return Confidence.HIGH
        if any(c == Confidence.LOW for c in confidences):
            return Confidence.LOW
        return Confidence.MEDIUM

    def _generate_portion_questions(self, items: list[FoodItem]) -> list[str]:
        """Generate clarifying questions about portions."""
        questions = []
        item_names = [item.name for item in items]
        if item_names:
            names_str = ", ".join(item_names)
            questions.append(f"Не хватает порций. Сколько примерно было: 1 тарелка, 2 котлеты, 150 г {item_names[0]}?")
        return questions

    def _extract_generic_items(self, text: str) -> list[FoodItem]:
        """Try to extract generic food items from text.

        This is a fallback when no specific food DB match is found.
        Looks for food-related words embedded in the text.
        """
        generic_food_words = [
            "еда", "еды", "food", "приём", "прием", "порци", "тарелк",
            "обед", "ужин", "завтрак", "перекус", "ланч", "подкрепил",
        ]
        if any(w in text for w in generic_food_words):
            return [
                FoodItem(
                    name="приём пищи",
                    portion_text="неизвестно",
                    calories_min=300,
                    calories_max=700,
                    protein_min_g=15.0,
                    protein_max_g=40.0,
                    confidence=Confidence.LOW,
                )
            ]
        return []
