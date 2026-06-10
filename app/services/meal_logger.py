"""Meal logger service — DB operations only. Response formatting is LLM's job."""

import datetime

from sqlalchemy import delete as sql_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Meal, MealItem
from app.db.repositories import get_or_create_user, save_meal, save_raw_message
from app.schemas.food import FoodAnalysis
from app.utils.time import format_meal_type


# ── In-memory context: last meal per user for append/update chaining ─────
_last_meal: dict[int, int] = {}


def set_last_meal(telegram_id: int, meal_id: int) -> None:
    _last_meal[telegram_id] = meal_id


def get_last_meal(telegram_id: int) -> int | None:
    return _last_meal.get(telegram_id)


def clear_last_meal(telegram_id: int) -> None:
    _last_meal.pop(telegram_id, None)


class MealLogger:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def ensure_user(self, telegram_id: int, username: str | None, first_name: str):
        user = await get_or_create_user(self._session, telegram_id=telegram_id, username=username, first_name=first_name)
        return user.id

    async def log_raw_message(self, user_id: int, telegram_message_id: int, message_type: str, text: str | None = None, photo_path: str | None = None) -> None:
        await save_raw_message(self._session, user_id=user_id, telegram_message_id=telegram_message_id, message_type=message_type, text=text, photo_path=photo_path)

    async def log_from_text(self, user_id: int, text: str, analysis: FoodAnalysis) -> tuple[Meal | None, str]:
        return await self._log_meal(user_id, "text", text, None, analysis)

    async def log_from_photo(self, user_id: int, photo_path: str, caption: str | None, analysis: FoodAnalysis) -> tuple[Meal | None, str]:
        return await self._log_meal(user_id, "photo_with_caption" if caption else "photo", caption, photo_path, analysis)

    async def _log_meal(self, user_id: int, source_type: str, original_text: str | None, photo_path: str | None, analysis: FoodAnalysis) -> tuple[Meal | None, str]:
        if not analysis.is_food:
            return None, "Не похоже на еду. Я ничего не записал."

        conf_str = analysis.confidence.value if hasattr(analysis.confidence, 'value') else str(analysis.confidence)

        items_data = [
            {"name": item.name, "portion_text": item.portion_text,
             "calories_min": item.calories_min, "calories_max": item.calories_max,
             "protein_min_g": item.protein_min_g, "protein_max_g": item.protein_max_g,
             "confidence": item.confidence.value if hasattr(item.confidence, 'value') else str(item.confidence)}
            for item in analysis.items
        ]

        meal = await save_meal(self._session, user_id=user_id, meal_type=analysis.meal_type.value,
                               source_type=source_type, original_text=original_text, photo_path=photo_path,
                               calories_min=analysis.total_calories_min, calories_max=analysis.total_calories_max,
                               protein_min_g=analysis.total_protein_min_g, protein_max_g=analysis.total_protein_max_g,
                               confidence=conf_str, status="confirmed", items_data=items_data)
        return meal, ""

    async def append_to_meal(self, meal_id: int, text: str, analysis: FoodAnalysis) -> tuple[Meal | None, str]:
        meal = await self._session.get(Meal, meal_id)
        if not meal:
            return None, "Не могу найти предыдущий приём."

        if meal.original_text:
            meal.original_text += "; " + text
        else:
            meal.original_text = text

        for item in analysis.items:
            self._session.add(MealItem(
                meal_id=meal.id, name=item.name, portion_text=item.portion_text,
                calories_min=item.calories_min, calories_max=item.calories_max,
                protein_min_g=item.protein_min_g, protein_max_g=item.protein_max_g,
                confidence=item.confidence.value if hasattr(item.confidence, 'value') else str(item.confidence),
            ))

        await self._session.flush()
        await self._session.refresh(meal, attribute_names=["items"])

        all_items = list(meal.items)
        meal.calories_min = sum(it.calories_min or 0 for it in all_items)
        meal.calories_max = sum(it.calories_max or 0 for it in all_items)
        meal.protein_min_g = sum(it.protein_min_g or 0 for it in all_items)
        meal.protein_max_g = sum(it.protein_max_g or 0 for it in all_items)
        meal.updated_at = datetime.datetime.utcnow()
        return meal, ""

    async def update_meal(self, meal_id: int, text: str, analysis: FoodAnalysis) -> tuple[Meal | None, str]:
        """Replace meal data entirely — user correction/clarification."""
        meal = await self._session.get(Meal, meal_id)
        if not meal:
            return None, "Не могу найти приём пищи для обновления."

        meal.meal_type = analysis.meal_type.value
        meal.original_text = text
        meal.confidence = analysis.confidence.value if hasattr(analysis.confidence, 'value') else str(analysis.confidence)
        if analysis.total_calories_min is not None:
            meal.calories_min = analysis.total_calories_min
            meal.calories_max = analysis.total_calories_max
            meal.protein_min_g = analysis.total_protein_min_g
            meal.protein_max_g = analysis.total_protein_max_g
        meal.updated_at = datetime.datetime.utcnow()

        # Replace items
        await self._session.execute(sql_delete(MealItem).where(MealItem.meal_id == meal_id))
        for item in analysis.items:
            self._session.add(MealItem(
                meal_id=meal.id, name=item.name, portion_text=item.portion_text,
                calories_min=item.calories_min, calories_max=item.calories_max,
                protein_min_g=item.protein_min_g, protein_max_g=item.protein_max_g,
                confidence=item.confidence.value if hasattr(item.confidence, 'value') else str(item.confidence),
            ))
        await self._session.flush()
        return meal, ""

    async def delete_last_meal(self, user_id: int) -> str | None:
        result = await self._session.execute(
            select(Meal).where(Meal.user_id == user_id).order_by(Meal.created_at.desc()).limit(1)
        )
        meal = result.scalar_one_or_none()
        if not meal:
            return None
        items_str = ", ".join(it.name for it in meal.items) if meal.items else "—"
        desc = f"{format_meal_type(meal.meal_type)}: {items_str}"
        await self._session.delete(meal)
        await self._session.flush()
        return desc

    async def delete_meal_by_number(self, user_id: int, n: int) -> str | None:
        now = datetime.datetime.utcnow()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        result = await self._session.execute(
            select(Meal).where(Meal.user_id == user_id, Meal.eaten_at >= today_start).order_by(Meal.eaten_at.asc())
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
