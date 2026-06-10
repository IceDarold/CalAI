"""Meal logger service — saves meal records to the database."""

import datetime
from typing import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Meal
from app.db.repositories import get_or_create_user, save_meal, save_raw_message
from app.schemas.food import Confidence, FoodAnalysis, MealStatus
from app.utils.time import format_meal_type


class MealLogger:
    """Logs meals to the database and generates user-facing responses."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def ensure_user(
        self, telegram_id: int, username: str | None, first_name: str
    ):
        """Ensure user exists in DB, return user ID."""
        user = await get_or_create_user(
            self._session,
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
        )
        return user.id

    async def log_from_text(
        self,
        user_id: int,
        text: str,
        analysis: FoodAnalysis,
    ) -> tuple[Meal, str]:
        """Save a meal from text analysis and return response text."""
        return await self._log_meal(
            user_id=user_id,
            source_type="text",
            original_text=text,
            photo_path=None,
            analysis=analysis,
        )

    async def log_from_photo(
        self,
        user_id: int,
        photo_path: str,
        caption: str | None,
        analysis: FoodAnalysis,
    ) -> tuple[Meal, str]:
        """Save a meal from photo analysis and return response text."""
        return await self._log_meal(
            user_id=user_id,
            source_type="photo_with_caption" if caption else "photo",
            original_text=caption,
            photo_path=photo_path,
            analysis=analysis,
        )

    async def log_raw_message(
        self,
        user_id: int,
        telegram_message_id: int,
        message_type: str,
        text: str | None = None,
        photo_path: str | None = None,
    ) -> None:
        """Save a raw incoming message to the DB."""
        await save_raw_message(
            self._session,
            user_id=user_id,
            telegram_message_id=telegram_message_id,
            message_type=message_type,
            text=text,
            photo_path=photo_path,
        )

    async def _log_meal(
        self,
        user_id: int,
        source_type: str,
        original_text: str | None,
        photo_path: str | None,
        analysis: FoodAnalysis,
    ) -> tuple[Meal, str]:
        """Internal: save meal and generate response."""
        if not analysis.is_food:
            # Not food — don't save
            return None, "Не похоже на еду. Я ничего не записал."

        # Determine status
        if analysis.confidence == Confidence.LOW:
            status = MealStatus.DRAFT.value
        else:
            status = MealStatus.CONFIRMED.value

        # Convert items to dicts
        items_data = [
            {
                "name": item.name,
                "portion_text": item.portion_text,
                "calories_min": item.calories_min,
                "calories_max": item.calories_max,
                "protein_min_g": item.protein_min_g,
                "protein_max_g": item.protein_max_g,
                "confidence": item.confidence.value,
            }
            for item in analysis.items
        ]

        meal = await save_meal(
            self._session,
            user_id=user_id,
            meal_type=analysis.meal_type.value,
            source_type=source_type,
            original_text=original_text,
            photo_path=photo_path,
            calories_min=analysis.total_calories_min,
            calories_max=analysis.total_calories_max,
            protein_min_g=analysis.total_protein_min_g,
            protein_max_g=analysis.total_protein_max_g,
            confidence=analysis.confidence.value,
            status=status,
            items_data=items_data,
        )

        response = self._format_response(analysis, status)
        return meal, response

    def _format_response(self, analysis: FoodAnalysis, status: str) -> str:
        """Format a user-facing response based on analysis."""
        items_str = ", ".join(item.name for item in analysis.items)
        meal_type_str = format_meal_type(analysis.meal_type.value)

        if status == MealStatus.DRAFT.value or analysis.confidence == Confidence.LOW:
            # Draft — ask for clarification
            lines = [f"Записал как {meal_type_str}: {items_str}."]
            if analysis.total_calories_min and analysis.total_calories_max:
                lines.append(
                    f"Примерно {analysis.total_calories_min}–{analysis.total_calories_max} ккал, "
                    f"белок {analysis.total_protein_min_g or 0:.0f}–{analysis.total_protein_max_g or 0:.0f} г."
                )
            lines.append("Уверенность низкая — порции примерные.")
            if analysis.questions:
                lines.append(analysis.questions[0])
            return "\n".join(lines)

        # Confirmed
        lines = [f"Записал как {meal_type_str}: {items_str}."]
        if analysis.total_calories_min and analysis.total_calories_max:
            lines.append(
                f"Оценка: {analysis.total_calories_min}–{analysis.total_calories_max} ккал, "
                f"белок {analysis.total_protein_min_g or 0:.0f}–{analysis.total_protein_max_g or 0:.0f} г."
            )

        confidence_notes = {
            "medium": "Уверенность средняя — порции примерные.",
            "high": "Уверенность высокая.",
        }
        if analysis.confidence.value in confidence_notes:
            lines.append(confidence_notes[analysis.confidence.value])

        return "\n".join(lines)
