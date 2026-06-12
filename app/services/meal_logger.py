"""Meal logger service — DB operations. Last meal stored in users table (survives restart)."""

import datetime

from sqlalchemy import delete as sql_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Meal, MealItem, User
from app.db.repositories import get_or_create_user, save_meal, save_raw_message
from app.schemas.food import FoodAnalysis
from app.utils.time import format_meal_type


def conf_str(val) -> str:
    """Extract string value from enum or plain string."""
    return val.value if hasattr(val, 'value') else str(val)


class MealLogger:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def ensure_user(self, telegram_id: int, username: str | None, first_name: str):
        user = await get_or_create_user(self._session, telegram_id=telegram_id, username=username, first_name=first_name)
        return user.id

    async def get_user(self, user_id: int) -> User | None:
        return await self._session.get(User, user_id)

    # ── Last meal (persisted in DB, survives restart) ────────────────────

    async def get_last_meal_id(self, user_id: int) -> int | None:
        user = await self._session.get(User, user_id)
        return user.last_meal_id if user else None

    async def set_last_meal_id(self, user_id: int, meal_id: int) -> None:
        user = await self._session.get(User, user_id)
        if user:
            user.last_meal_id = meal_id

    async def clear_last_meal_id(self, user_id: int) -> None:
        user = await self._session.get(User, user_id)
        if user:
            user.last_meal_id = None

    # ── Profile ──────────────────────────────────────────────────────────

    async def set_profile(self, user_id: int, **kwargs) -> User:
        from app.services.calculator import calc_tdee, calc_targets
        user = await self._session.get(User, user_id)
        if not user:
            raise ValueError("User not found")
        for key, val in kwargs.items():
            if val is not None and hasattr(user, key):
                setattr(user, key, val)
        if all(getattr(user, x) for x in ['height_cm', 'weight_kg', 'age', 'gender']) and user.goal:
            tdee = calc_tdee(user.weight_kg, user.height_cm, user.age, user.gender)
            targets = calc_targets(tdee, user.goal, user.weight_kg)
            user.target_kcal = targets["kcal"]
            user.target_protein_g = targets["protein_g"]
            user.target_fat_g = targets["fat_g"]
            user.target_carbs_g = targets["carbs_g"]
        await self._session.flush()
        return user

    # ── Raw messages ─────────────────────────────────────────────────────

    async def log_raw_message(self, user_id: int, telegram_message_id: int, message_type: str,
                               text: str | None = None, photo_path: str | None = None) -> None:
        await save_raw_message(self._session, user_id=user_id, telegram_message_id=telegram_message_id,
                               message_type=message_type, text=text, photo_path=photo_path)

    # ── Meal CRUD ────────────────────────────────────────────────────────

    async def log_from_text(self, user_id: int, text: str, analysis: FoodAnalysis,
                            eaten_at: datetime.datetime | None = None) -> tuple[Meal | None, str]:
        return await self._log_meal(user_id, "text", text, None, analysis, eaten_at)

    async def log_from_photo(self, user_id: int, photo_path: str, caption: str | None,
                             analysis: FoodAnalysis) -> tuple[Meal | None, str]:
        return await self._log_meal(user_id, "photo_with_caption" if caption else "photo", caption, photo_path, analysis)

    async def _log_meal(self, user_id: int, source_type: str, original_text: str | None,
                        photo_path: str | None, analysis: FoodAnalysis,
                        eaten_at: datetime.datetime | None = None) -> tuple[Meal | None, str]:
        if not analysis.is_food:
            return None, "Не похоже на еду. Я ничего не записал."

        meal = await save_meal(self._session, user_id=user_id, analysis=analysis,
                               source_type=source_type, original_text=original_text,
                               photo_path=photo_path, eaten_at=eaten_at)
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
                confidence=conf_str(item.confidence),
            ))

        await self._session.flush()
        await self._session.refresh(meal, attribute_names=["items"])

        all_items = list(meal.items)
        for attr in ['calories', 'protein', 'fat', 'carbs']:
            setattr(meal, f'{attr}_min', sum(getattr(it, f'{attr}_min') or 0 for it in all_items))
            setattr(meal, f'{attr}_max', sum(getattr(it, f'{attr}_max') or 0 for it in all_items))
        meal.updated_at = datetime.datetime.utcnow()
        return meal, ""

    async def update_meal(self, meal_id: int, text: str, analysis: FoodAnalysis) -> tuple[Meal | None, str]:
        meal = await self._session.get(Meal, meal_id)
        if not meal:
            return None, "Не могу найти приём пищи."

        meal.meal_type = analysis.meal_type.value
        meal.original_text = text
        meal.confidence = conf_str(analysis.confidence)
        if analysis.total_calories_min is not None:
            for attr in ['calories', 'protein', 'fat', 'carbs']:
                setattr(meal, f'{attr}_min', getattr(analysis, f'total_{attr}_min'))
                setattr(meal, f'{attr}_max', getattr(analysis, f'total_{attr}_max'))
        meal.updated_at = datetime.datetime.utcnow()

        await self._session.execute(sql_delete(MealItem).where(MealItem.meal_id == meal_id))
        for item in analysis.items:
            self._session.add(MealItem(
                meal_id=meal.id, name=item.name, portion_text=item.portion_text,
                calories_min=item.calories_min, calories_max=item.calories_max,
                protein_min_g=item.protein_min_g, protein_max_g=item.protein_max_g,
                fat_min_g=item.fat_min_g, fat_max_g=item.fat_max_g,
                carbs_min_g=item.carbs_min_g, carbs_max_g=item.carbs_max_g,
                confidence=conf_str(item.confidence),
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
        await self._session.delete(meal)
        await self._session.flush()
        return desc

    async def delete_meal_by_number(self, user_id: int, n: int) -> str | None:
        now = datetime.datetime.utcnow()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        result = await self._session.execute(
            select(Meal).where(Meal.user_id == user_id, Meal.eaten_at >= today_start).order_by(Meal.eaten_at.asc()))
        meals = result.scalars().all()
        if n < 1 or n > len(meals):
            return None
        meal = meals[n - 1]
        items_str = ", ".join(it.name for it in meal.items) if meal.items else "—"
        desc = f"{format_meal_type(meal.meal_type)}: {items_str}"
        await self._session.delete(meal)
        await self._session.flush()
        return desc
