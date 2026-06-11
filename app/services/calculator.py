"""Deterministic nutrition calculator — kcal, protein, fat, carbs from USDA data."""

from dataclasses import dataclass


@dataclass
class CalculatedItem:
    input_name: str
    matched_food: dict | None
    grams: float
    kcal: float
    protein_g: float
    fat_g: float
    carbs_g: float
    confidence: str


@dataclass
class CalculationResult:
    items: list[CalculatedItem]
    total_kcal: float
    total_protein_g: float
    total_fat_g: float
    total_carbs_g: float
    confidence: str
    questions: list[str]


def calculate_from_parsed(
    parsed_items: list[dict],
    food_matches: list[dict | None],
) -> CalculationResult:
    items: list[CalculatedItem] = []
    total_kcal = 0.0; total_protein = 0.0; total_fat = 0.0; total_carbs = 0.0
    questions: list[str] = []; scores: list[int] = []

    for parsed, match in zip(parsed_items, food_matches):
        grams = parsed.get("grams", 0)
        if grams <= 0:
            grams = 100
            questions.append(f"Сколько грамм {parsed.get('name', '')}?")

        if match:
            kcal = match["kcal_per_100g"] * grams / 100
            protein = match["protein_per_100g"] * grams / 100
            fat = match["fat_per_100g"] * grams / 100
            carbs = match["carbs_per_100g"] * grams / 100
            conf = "high"; s = 3
        else:
            kcal = _fallback_kcal(parsed.get("name", ""), grams)
            protein = grams * 0.10; fat = grams * 0.05; carbs = grams * 0.15
            conf = "low"; s = 1
            questions.append(f"Не нашёл '{parsed.get('name', '')}' в базе.")

        if parsed.get("grams_confidence") == "low":
            conf = "low"; s = min(s, 1)

        items.append(CalculatedItem(
            input_name=parsed.get("name", "?"), matched_food=match, grams=grams,
            kcal=round(kcal), protein_g=round(protein, 1),
            fat_g=round(fat, 1), carbs_g=round(carbs, 1), confidence=conf,
        ))
        total_kcal += kcal; total_protein += protein; total_fat += fat; total_carbs += carbs
        scores.append(s)

    avg = sum(scores) / max(len(scores), 1)
    overall = "high" if avg >= 2.5 else "medium" if avg >= 1.5 else "low"

    return CalculationResult(items=items, total_kcal=round(total_kcal),
        total_protein_g=round(total_protein, 1), total_fat_g=round(total_fat, 1),
        total_carbs_g=round(total_carbs, 1), confidence=overall, questions=questions)


def _fallback_kcal(name: str, grams: float) -> float:
    n = name.lower()
    if any(w in n for w in ["кур", "мяс", "рыб", "стейк", "говяд", "свин", "индей"]): return grams * 1.8
    if any(w in n for w in ["рис", "греч", "макар", "хлеб", "бул", "каш", "карто"]): return grams * 1.2
    if any(w in n for w in ["масл", "орех", "семеч", "авокад", "сал", "жир"]): return grams * 5.0
    return grams * 1.5


def calc_tdee(weight_kg: float, height_cm: float, age: int, gender: str) -> int:
    """Mifflin-St Jeor equation for BMR, ×1.2 for sedentary TDEE."""
    if gender == "female":
        bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age - 161
    else:
        bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age + 5
    return int(bmr * 1.2)


def calc_targets(tdee: int, goal: str, weight_kg: float) -> dict:
    """Calculate target macros based on TDEE and goal."""
    if goal == "cut":
        kcal = int(tdee * 0.8)
    elif goal == "bulk":
        kcal = int(tdee * 1.15)
    else:
        kcal = tdee

    protein_g = int(weight_kg * 2.0)    # 2g per kg
    fat_g = int(kcal * 0.25 / 9)        # 25% from fat
    carbs_g = int((kcal - protein_g * 4 - fat_g * 9) / 4)  # rest from carbs

    return {"kcal": kcal, "protein_g": max(protein_g, 60),
            "fat_g": max(fat_g, 30), "carbs_g": max(carbs_g, 50)}
