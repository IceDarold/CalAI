"""Meal logger service — saves meals, formats responses."""

import datetime
from typing import Sequence

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Meal, MealItem
from app.db.repositories import get_or_create_user, save_meal, save_raw_message
from app.schemas.food import Confidence, FoodAnalysis, MealStatus
from app.utils.time import format_meal_type


# ── In-memory context: last meal per user for "и ещё" chaining ───────────
_last_meal: dict[int, int] = {}  # telegram_id → meal_id


def set_last_meal(telegram_id: int, meal_id: int) -> None:
    _last_meal[telegram_id] = meal_id


def get_last_meal(telegram_id: int) -> int | None:
    return _last_meal.get(telegram_id)


def clear_last_meal(telegram_id: int) -> None:
    _last_meal.pop(telegram_id, None)


class MealLogger:
    """Logs meals to the database and generates user-facing responses."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def ensure_user(
        self, telegram_id: int, username: str | None, first_name: str
    ):
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
        await save_raw_message(
            self._session,
            user_id=user_id,
            telegram_message_id=telegram_message_id,
            message_type=message_type,
            text=text,
            photo_path=photo_path,
        )

    async def append_to_meal(
        self,
        meal_id: int,
        text: str,
        analysis: FoodAnalysis,
    ) -> tuple[Meal, str]:
        """Append new items to an existing meal."""
        meal = await self._session.get(Meal, meal_id)
        if not meal:
            return None, "Не могу найти предыдущий приём пищи."

        # Update the original text
        if meal.original_text:
            meal.original_text += "; " + text
        else:
            meal.original_text = text

        # Add new items
        items_data = [
            {
                "name": item.name,
                "portion_text": item.portion_text,
                "calories_min": item.calories_min,
                "calories_max": item.calories_max,
                "protein_min_g": item.protein_min_g,
                "protein_max_g": item.protein_max_g,
                "confidence": item.confidence.value if hasattr(item.confidence, 'value') else item.confidence,
            }
            for item in analysis.items
        ]

        for item_data in items_data:
            new_item = MealItem(
                meal_id=meal.id,
                name=item_data["name"],
                portion_text=item_data.get("portion_text"),
                calories_min=item_data.get("calories_min"),
                calories_max=item_data.get("calories_max"),
                protein_min_g=item_data.get("protein_min_g"),
                protein_max_g=item_data.get("protein_max_g"),
                confidence=item_data.get("confidence", "medium"),
            )
            self._session.add(new_item)

        # Recalculate totals
        await self._session.flush()
        await self._session.refresh(meal, attribute_names=["items"])

        all_items = list(meal.items)
        meal.calories_min = sum(it.calories_min or 0 for it in all_items)
        meal.calories_max = sum(it.calories_max or 0 for it in all_items)
        meal.protein_min_g = sum(it.protein_min_g or 0 for it in all_items)
        meal.protein_max_g = sum(it.protein_max_g or 0 for it in all_items)
        meal.updated_at = datetime.datetime.utcnow()

        items_str = ", ".join(it.name for it in all_items)
        response = (
            f"Добавил к приёму пищи: {items_str}.\n"
            f"Оценка: {meal.calories_min}–{meal.calories_max} ккал, "
            f"белок {meal.protein_min_g:.0f}–{meal.protein_max_g:.0f} г.\n"
            f"Это диапазон, потому что точный вес неизвестен. Напиши граммы — посчитаю точнее."
        )
        return meal, response

    async def delete_last_meal(self, user_id: int) -> str | None:
        """Delete the most recent meal for the user. Returns meal description or None."""
        result = await self._session.execute(
            select(Meal)
            .where(Meal.user_id == user_id)
            .order_by(Meal.created_at.desc())
            .limit(1)
        )
        meal = result.scalar_one_or_none()
        if not meal:
            return None

        items_str = ", ".join(it.name for it in meal.items) if meal.items else "—"
        desc = f"{format_meal_type(meal.meal_type)}: {items_str}"

        await self._session.delete(meal)
        await self._session.flush()
        return desc

    async def delete_meal_by_number(
        self, user_id: int, n: int
    ) -> str | None:
        """Delete the n-th meal (1-indexed) from today. Returns description or None."""
        now = datetime.datetime.utcnow()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        result = await self._session.execute(
            select(Meal)
            .where(Meal.user_id == user_id, Meal.eaten_at >= today_start)
            .order_by(Meal.eaten_at.asc())
        )
        meals = result.scalars().all()

        if n < 1 or n > len(meals):
            return None

        meal = meals[n - 1]
        items_str = ", ".join(it.name for it in meal.items) if meal.items else "—"
        desc = f"{format_meal_type(meal.meal_type)}: {items_str}"

        await self._session.delete(meal)
        await self._session.flush()
        return desc

    async def get_draft_count(self, user_id: int) -> int:
        """Count draft meals for the user."""
        result = await self._session.execute(
            select(Meal).where(Meal.user_id == user_id, Meal.status == "draft")
        )
        return len(result.scalars().all())

    async def _log_meal(
        self,
        user_id: int,
        source_type: str,
        original_text: str | None,
        photo_path: str | None,
        analysis: FoodAnalysis,
    ) -> tuple[Meal, str]:
        if not analysis.is_food:
            return None, "Не похоже на еду. Я ничего не записал."

        status = (
            MealStatus.DRAFT.value
            if analysis.confidence == Confidence.LOW
            else MealStatus.CONFIRMED.value
        )

        items_data = [
            {
                "name": item.name,
                "portion_text": item.portion_text,
                "calories_min": item.calories_min,
                "calories_max": item.calories_max,
                "protein_min_g": item.protein_min_g,
                "protein_max_g": item.protein_max_g,
                "confidence": item.confidence.value if hasattr(item.confidence, 'value') else item.confidence,
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
            confidence=analysis.confidence.value if hasattr(analysis.confidence, 'value') else analysis.confidence,
            status=status,
            items_data=items_data,
        )

        response = self._format_response(analysis, status)
        return meal, response

    def _format_response(self, analysis: FoodAnalysis, status: str) -> str:
        items_str = ", ".join(item.name for item in analysis.items)
        meal_type_str = format_meal_type(analysis.meal_type.value)

        if status == MealStatus.DRAFT.value or analysis.confidence == Confidence.LOW:
            lines = [f"Записал как {meal_type_str}: {items_str}."]
            if analysis.total_calories_min and analysis.total_calories_max:
                lines.append(
                    f"Примерно {analysis.total_calories_min}–{analysis.total_calories_max} ккал, "
                    f"белок {analysis.total_protein_min_g:.0f}–{analysis.total_protein_max_g:.0f} г."
                )
            lines.append("Это диапазон, потому что точный вес неизвестен. Напиши граммы — посчитаю точнее.")
            if analysis.questions:
                lines.append(analysis.questions[0])
            return "\n".join(lines)

        lines = [f"Записал как {meal_type_str}: {items_str}."]
        if analysis.total_calories_min and analysis.total_calories_max:
            lines.append(
                f"Оценка: {analysis.total_calories_min}–{analysis.total_calories_max} ккал, "
                f"белок {analysis.total_protein_min_g:.0f}–{analysis.total_protein_max_g:.0f} г."
            )

        confidence_notes = {
            "medium": "Это диапазон — точный вес неизвестен. Уточни граммы, и я посчитаю точнее.",
            "high": "Уверенность высокая.",
        }
        if analysis.confidence.value in confidence_notes:
            lines.append(confidence_notes[analysis.confidence.value])

        return "\n".join(lines)
