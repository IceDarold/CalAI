"""Nutrition pipeline — USDA lookup + deterministic calculator.

Centralizes the ±15% range logic in one place.
"""

from app.schemas.food import (
    ParsedFoodItem, Confidence as C, FoodAnalysis as FA, FoodItem as FI, MealType as MT,
)
from app.services.calculator import calculate_from_parsed

# Single source of truth for range calculation
RANGE_LOW = 0.85   # −15% for USDA-estimated portions
RANGE_HIGH = 1.15
RANGE_LOW_EXACT = 0.95   # ±5% for user-provided or label data
RANGE_HIGH_EXACT = 1.05


def apply_range(value: float, is_exact: bool = False) -> tuple[float, float]:
    """Apply confidence range to a value. Exact data gets ±5%, estimated gets ±15%."""
    lo, hi = (RANGE_LOW_EXACT, RANGE_HIGH_EXACT) if is_exact else (RANGE_LOW, RANGE_HIGH)
    return round(value * lo), round(value * hi)


async def run_nutrition_pipeline(session, result: dict):
    """LLM items → USDA lookup → calculator → FoodAnalysis."""
    from app.services.food_db import search_food

    parsed = [ParsedFoodItem(
        name_ru=it.get("name_ru", it.get("name", "")),
        name_en=it.get("name_en", it.get("name", "")),
        grams=float(it.get("grams", 100)),
        grams_confidence=it.get("grams_confidence", "medium"),
        portion_text=it.get("portion_text", ""),
        manual_kcal=it.get("manual_kcal"),
        manual_protein_g=it.get("manual_protein_g"),
        manual_fat_g=it.get("manual_fat_g"),
        manual_carbs_g=it.get("manual_carbs_g"),
    ) for it in result.get("items", [])]

    # USDA lookup (skip for manual items)
    food_matches = []
    for pi in parsed:
        if pi.is_manual:
            food_matches.append(None)
        else:
            matches = await search_food(session, pi.name_en or pi.name_ru, limit=1)
            food_matches.append(matches[0] if matches else None)

    calc = calculate_from_parsed([p.model_dump() for p in parsed], food_matches)

    conf = C(result.get("confidence", "medium"))
    mt = MT(result.get("meal_type", "unknown"))
    is_exact = all(pi.is_manual for pi in parsed)

    items = [FI(
        name=pi.name_ru or pi.name_en,
        portion_text=pi.portion_text or f"~{ci.grams:.0f} г",
        calories_min=apply_range(ci.kcal, is_exact)[0],
        calories_max=apply_range(ci.kcal, is_exact)[1],
        protein_min_g=round(ci.protein_g * (RANGE_LOW_EXACT if is_exact else RANGE_LOW), 1),
        protein_max_g=round(ci.protein_g * (RANGE_HIGH_EXACT if is_exact else RANGE_HIGH), 1),
        fat_min_g=round(ci.fat_g * (RANGE_LOW_EXACT if is_exact else RANGE_LOW), 1),
        fat_max_g=round(ci.fat_g * (RANGE_HIGH_EXACT if is_exact else RANGE_HIGH), 1),
        carbs_min_g=round(ci.carbs_g * (RANGE_LOW_EXACT if is_exact else RANGE_LOW), 1),
        carbs_max_g=round(ci.carbs_g * (RANGE_HIGH_EXACT if is_exact else RANGE_HIGH), 1),
        confidence=C(ci.confidence),
    ) for pi, ci in zip(parsed, calc.items)]

    analysis = FA(
        is_food=True, meal_type=mt, items=items,
        total_calories_min=apply_range(calc.total_kcal, is_exact)[0],
        total_calories_max=apply_range(calc.total_kcal, is_exact)[1],
        total_protein_min_g=round(calc.total_protein_g * (RANGE_LOW_EXACT if is_exact else RANGE_LOW), 1),
        total_protein_max_g=round(calc.total_protein_g * (RANGE_HIGH_EXACT if is_exact else RANGE_HIGH), 1),
        total_fat_min_g=round(calc.total_fat_g * (RANGE_LOW_EXACT if is_exact else RANGE_LOW), 1),
        total_fat_max_g=round(calc.total_fat_g * (RANGE_HIGH_EXACT if is_exact else RANGE_HIGH), 1),
        total_carbs_min_g=round(calc.total_carbs_g * (RANGE_LOW_EXACT if is_exact else RANGE_LOW), 1),
        total_carbs_max_g=round(calc.total_carbs_g * (RANGE_HIGH_EXACT if is_exact else RANGE_HIGH), 1),
        confidence=conf, parsed_items=parsed,
    )
    return items, parsed, analysis
