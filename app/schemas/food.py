"""Pydantic schemas for food analysis."""

from enum import Enum

from pydantic import BaseModel, Field


class MealType(str, Enum):
    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    DINNER = "dinner"
    SNACK = "snack"
    UNKNOWN = "unknown"


class Confidence(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class GoalType(str, Enum):
    CUT = "cut"         # дефицит
    MAINTAIN = "maintain"  # поддержание
    BULK = "bulk"       # набор


class ParsedFoodItem(BaseModel):
    name_ru: str = ""
    name_en: str = ""
    grams: float = 100
    grams_confidence: str = "medium"
    portion_text: str = ""

    @property
    def name(self) -> str:
        return self.name_ru or self.name_en


class FoodItem(BaseModel):
    name: str
    portion_text: str | None = None
    calories_min: int | None = None
    calories_max: int | None = None
    protein_min_g: float | None = None
    protein_max_g: float | None = None
    fat_min_g: float | None = None
    fat_max_g: float | None = None
    carbs_min_g: float | None = None
    carbs_max_g: float | None = None
    confidence: Confidence = Confidence.MEDIUM


class FoodAnalysis(BaseModel):
    is_food: bool = True
    meal_type: MealType = MealType.UNKNOWN
    items: list[FoodItem] = Field(default_factory=list)
    total_calories_min: int | None = None
    total_calories_max: int | None = None
    total_protein_min_g: float | None = None
    total_protein_max_g: float | None = None
    total_fat_min_g: float | None = None
    total_fat_max_g: float | None = None
    total_carbs_min_g: float | None = None
    total_carbs_max_g: float | None = None
    confidence: Confidence = Confidence.MEDIUM
    questions: list[str] = Field(default_factory=list)
    raw_response: str | None = None
    parsed_items: list[ParsedFoodItem] = Field(default_factory=list, exclude=True)


class UserProfile(BaseModel):
    """User's physical profile and goals."""
    height_cm: float | None = None
    weight_kg: float | None = None
    age: int | None = None
    gender: str | None = None  # "male" / "female"
    goal: GoalType = GoalType.MAINTAIN
    target_kcal: int | None = None
    target_protein_g: int | None = None
    target_fat_g: int | None = None
    target_carbs_g: int | None = None
