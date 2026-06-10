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


class SourceType(str, Enum):
    TEXT = "text"
    PHOTO = "photo"
    PHOTO_WITH_CAPTION = "photo_with_caption"


class MealStatus(str, Enum):
    CONFIRMED = "confirmed"
    DRAFT = "draft"


class FoodItem(BaseModel):
    """A single food item within a meal."""
    name: str
    portion_text: str | None = None
    calories_min: int | None = None
    calories_max: int | None = None
    protein_min_g: float | None = None
    protein_max_g: float | None = None
    confidence: Confidence = Confidence.MEDIUM


class FoodAnalysis(BaseModel):
    """Result of food analysis from text or photo."""
    is_food: bool = True
    meal_type: MealType = MealType.UNKNOWN
    items: list[FoodItem] = Field(default_factory=list)
    total_calories_min: int | None = None
    total_calories_max: int | None = None
    total_protein_min_g: float | None = None
    total_protein_max_g: float | None = None
    confidence: Confidence = Confidence.MEDIUM
    questions: list[str] = Field(default_factory=list)
    raw_response: str | None = None
