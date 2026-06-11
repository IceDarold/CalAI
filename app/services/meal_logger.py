"""Meal logger service — DB operations only. Response formatting is LLM's job."""

import datetime

from sqlalchemy import delete as sql_delete, select, update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Meal, MealItem, User
from app.db.repositories import get_or_create_user, save_meal, save_raw_message
from app.schemas.food import FoodAnalysis, GoalType
from app.services.calculator import calc_tdee, calc_targets
from app.utils.time import format_meal_type


_last_meal: dict[int, int] = {}

def set_last_meal(telegram_id: int, meal_id: int) -> None:
    _last_meal[telegram_id] = meal_id

def get_last_meal(telegram_id: int) -> int | None:
    return _last_meal.get(telegram_id)

def clear_last_meal(telegram_id: int) -> None:
    _last_meal.pop(telegram_id, None)


def _conf_str(val) -> str:
    return val.value if hasattr(val, 'value') else str(val)


class MealLogger:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def ensure_user(self, telegram_id: int, username: str | None, first_name: str):
        user = await get_or_create_user(self._session, telegram_id=telegram_id, username=username, first_name=first_name)
        return user.id

    async def get_user(self, user_id: int) -> User | None:
        return await self._session.get(User, user_id)

    async def set_profile(self, user_id: int, **kwargs) -> User:
        """Update user profile fields. Auto-calculates TDEE and targets if height/weight/age/gender/goal present."""
        user = await self._session.get(User, user_id)
        if not user:
            raise ValueError("User not found")

        for key, val in kwargs.items():
            if val is not None and hasattr(user, key):
                setattr(user, key, val)

        # Recalculate targets if we have enough data
        if all(getattr(user, x) for x in ['height_cm', 'weight_kg', 'age', 'gender']) and user.goal:
            tdee = calc_tdee(user.weight_kg, user.height_cm, user.age, user.gender)
            targets = calc_targets(tdee, user.goal, user.weight_kg)
            user.target_kcal = targets["kcal"]
            user.target_protein_g = targets["protein_g"]
            user.target_fat_g = targets["fat_g"]
            user.target_carbs_g = targets["carbs_g"]

        await self._session.flush()
        return user

    async def log_raw_message(self, user_id: int, telegram_message_id: int, message_type: str, text: str | None = None, photo_path: str | None = None) -> None:
        await save_raw_message(self._session, user_id=user_id, telegram_message_id=telegram_message_id, message_type=message_type, text=text, photo_path=photo_path)

    async def log_from_text(self, user_id: int, text: str, analysis: FoodAnalysis, eaten_at: datetime.datetime | None = None) -> tuple[Meal | None, str]:
        return await self._log_meal(user_id, "text", text, None, analysis, eaten_at)

    async def log_from_photo(self, user_id: int, photo_path: str, caption: str | None, analysis: FoodAnalysis) -> tuple[Meal | None, str]:
        return await self._log_meal(user_id, "photo_with_caption" if caption else "photo", caption, photo_path, analysis)

    async def _log_meal(self, user_id: int, source_type: str, original_text: str | None, photo_path: str | None, analysis: FoodAnalysis, eaten_at: datetime.datetime | None = None) -> tuple[Meal | None, str]:
        if not analysis.is_food:
            return None, "Не похоже на еду. Я ничего не записал."

        items_data = [
            {"name": item.name, "portion_text": item.portion_text,
             "calories_min": item.calories_min, "calories_max": item.calories_max,
             "protein_min_g": item.protein_min_g, "protein_max_g": item.protein_max_g,
             "fat_min_g": item.fat_min_g, "fat_max_g": item.fat_max_g,
             "carbs_min_g": item.carbs_min_g, "carbs_max_g": item.carbs_max_g,
             "confidence": _conf_str(item.confidence)}
            for item in analysis.items
        ]

        meal = await save_meal(self._session, user_id=user_id, meal_type=analysis.meal_type.value,
                               source_type=source_type, original_text=original_text, photo_path=photo_path,
                               calories_min=analysis.total_calories_min, calories_max=analysis.total_calories_max,
                               protein_min_g=analysis.total_protein_min_g, protein_max_g=analysis.total_protein_max_g,
                               fat_min_g=analysis.total_fat_min_g, fat_max_g=analysis.total_fat_max_g,
                               carbs_min_g=analysis.total_carbs_min_g, carbs_max_g=analysis.total_carbs_max_g,
                               confidence=_conf_str(analysis.confidence), status="confirmed",
                               items_data=items_data, eaten_at=eaten_at)
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
                fat_min_g=item.fat_min_g, fat_max_g=item.fat_max_g,
                carbs_min_g=item.carbs_min_g, carbs_max_g=item.carbs_max_g,
                confidence=_conf_str(item.confidence),
            ))

        await self._session.flush()
        await self._session.refresh(meal, attribute_names=["items"])

        all_items = list(meal.items)
        meal.calories_min = sum(it.calories_min or 0 for it in all_items)
        meal.calories_max = sum(it.calories_max or 0 for it in all_items)
        meal.protein_min_g = sum(it.protein_min_g or 0 for it in all_items)
        meal.protein_max_g = sum(it.protein_max_g or 0 for it in all_items)
        meal.fat_min_g = sum(it.fat_min_g or 0 for it in all_items)
        meal.fat_max_g = sum(it.fat_max_g or 0 for it in all_items)
        meal.carbs_min_g = sum(it.carbs_min_g or 0 for it in all_items)
        meal.carbs_max_g = sum(it.carbs_max_g or 0 for it in all_items)
        meal.updated_at = datetime.datetime.utcnow()
        return meal, ""

    async def update_meal(self, meal_id: int, text: str, analysis: FoodAnalysis) -> tuple[Meal | None, str]:
        meal = await self._session.get(Meal, meal_id)
        if not meal:
            return None, "Не могу найти приём пищи."

        meal.meal_type = analysis.meal_type.value
        meal.original_text = text
        meal.confidence = _conf_str(analysis.confidence)
        if analysis.total_calories_min is not None:
            meal.calories_min = analysis.total_calories_min
            meal.calories_max = analysis.total_calories_max
            meal.protein_min_g = analysis.total_protein_min_g
            meal.protein_max_g = analysis.total_protein_max_g
            meal.fat_min_g = analysis.total_fat_min_g
            meal.fat_max_g = analysis.total_fat_max_g
            meal.carbs_min_g = analysis.total_carbs_min_g
            meal.carbs_max_g = analysis.total_carbs_max_g
        meal.updated_at = datetime.datetime.utcnow()

        await self._session.execute(sql_delete(MealItem).where(MealItem.meal_id == meal_id))
        for item in analysis.items:
            self._session.add(MealItem(
                meal_id=meal.id, name=item.name, portion_text=item.portion_text,
                calories_min=item.calories_min, calories_max=item.calories_max,
                protein_min_g=item.protein_min_g, protein_max_g=item.protein_max_g,
                fat_min_g=item.fat_min_g, fat_max_g=item.fat_max_g,
                carbs_min_g=item.carbs_min_g, carbs_max_g=item.carbs_max_g,
                confidence=_conf_str(item.confidence),
            ))
        await self._session.flush()
        return meal, ""

    async def delete_last_meal(self, user_id: int) -> str | None:
        result = await self._session.execute(
            select(Meal).where(Meal.user_id == user_id).order_by(Meal.created_at.desc()).limit(1))
        meal = result.scalar_one_or_none()
        if not meal:
            return None
        items_str = ", ".join(it.name for it in meal.items) if meal.items else "—"
        desc = f"{format_meal_type(meal.meal_type)}: {items_str}"
        await self._session.delete(meal); await self._session.flush()
        return desc

    async def delete_meal_by_number(self, user_id: int, n: int) -> str | None:
        now = datetime.datetime.utcnow()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        result = await self._session.execute(
            select(Meal).where(Meal.user_id == user_id, Meal.eaten_at >= today_start).order_by(Meal.eaten_at.asc()))
        meals = result.scalars().all()
        if n < 1 or n > len(meals): return None
        meal = meals[n - 1]
        items_str = ", ".join(it.name for it in meal.items) if meal.items else "—"
        desc = f"{format_meal_type(meal.meal_type)}: {items_str}"
        await self._session.delete(meal); await self._session.flush()
        return desc
