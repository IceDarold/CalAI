"""Deterministic nutrition calculator.

Takes parsed food items (name + grams) from LLM, looks up USDA nutrition data,
and calculates totals. No AI guessing — math only.
"""

from dataclasses import dataclass


@dataclass
class CalculatedItem:
    """A single food item with calculated nutrition."""
    input_name: str  # what the user/LLM called it
    matched_food: dict | None  # USDA match or None
    grams: float
    kcal: float
    protein_g: float
    fat_g: float
    carbs_g: float
    confidence: str  # high/medium/low


@dataclass
class CalculationResult:
    """Full nutrition calculation result."""
    items: list[CalculatedItem]
    total_kcal: float
    total_protein_g: float
    total_fat_g: float
    total_carbs_g: float
    confidence: str  # overall confidence
    questions: list[str]


def calculate_from_parsed(
    parsed_items: list[dict],
    food_matches: list[dict | None],
) -> CalculationResult:
    """Calculate nutrition from LLM-parsed items and USDA matches.

    Args:
        parsed_items: LLM output [{"name": "курица", "grams": 150}, ...]
        food_matches: Corresponding USDA matches (dict or None if not found)

    Returns:
        CalculationResult with totals and per-item breakdown.
    """
    items: list[CalculatedItem] = []
    total_kcal = 0.0
    total_protein = 0.0
    total_fat = 0.0
    total_carbs = 0.0
    questions: list[str] = []
    confidence_scores: list[int] = []  # high=3, medium=2, low=1

    for parsed, match in zip(parsed_items, food_matches):
        grams = parsed.get("grams", 0)
        if grams <= 0:
            grams = 100  # default guess
            questions.append(f"Сколько грамм {parsed.get('name', '')}?")

        if match:
            kcal = match["kcal_per_100g"] * grams / 100
            protein = match["protein_per_100g"] * grams / 100
            fat = match["fat_per_100g"] * grams / 100
            carbs = match["carbs_per_100g"] * grams / 100
            conf = "high"
            conf_score = 3
        else:
            # No USDA match — fallback estimate
            kcal = _fallback_kcal(parsed.get("name", ""), grams)
            protein = grams * 0.10  # rough 10% protein
            fat = grams * 0.05
            carbs = grams * 0.15
            conf = "low"
            conf_score = 1
            questions.append(f"Не нашёл '{parsed.get('name', '')}' в базе. Что это примерно?")

        # Adjust confidence based on gram precision
        if grams == 100 or parsed.get("grams_confidence") == "low":
            conf = "low" if conf == "medium" else conf
            conf_score = min(conf_score, 1)

        items.append(CalculatedItem(
            input_name=parsed.get("name", "?"),
            matched_food=match,
            grams=grams,
            kcal=round(kcal),
            protein_g=round(protein, 1),
            fat_g=round(fat, 1),
            carbs_g=round(carbs, 1),
            confidence=conf,
        ))
        total_kcal += kcal
        total_protein += protein
        total_fat += fat
        total_carbs += carbs
        confidence_scores.append(conf_score)

    # Overall confidence
    avg_score = sum(confidence_scores) / max(len(confidence_scores), 1)
    if avg_score >= 2.5:
        overall_conf = "high"
    elif avg_score >= 1.5:
        overall_conf = "medium"
    else:
        overall_conf = "low"

    return CalculationResult(
        items=items,
        total_kcal=round(total_kcal),
        total_protein_g=round(total_protein, 1),
        total_fat_g=round(total_fat, 1),
        total_carbs_g=round(total_carbs, 1),
        confidence=overall_conf,
        questions=questions,
    )


def _fallback_kcal(food_name: str, grams: float) -> float:
    """Very rough kcal estimate when USDA lookup fails."""
    name_lower = food_name.lower()
    # Protein-rich
    if any(w in name_lower for w in ["кур", "мяс", "рыб", "стейк", "говяд", "свин", "индей"]):
        return grams * 1.8
    # Grain/carb-rich
    if any(w in name_lower for w in ["рис", "греч", "макар", "хлеб", "бул", "каш", "карто"]):
        return grams * 1.2
    # Fatty
    if any(w in name_lower for w in ["масл", "орех", "семеч", "авокад", "сал", "жир"]):
        return grams * 5.0
    # Default
    return grams * 1.5
