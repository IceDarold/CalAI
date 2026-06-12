"""Context builder — collects all data the LLM needs to make decisions."""

import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Meal, RawMessage, User
from app.utils.time import format_meal_type


async def build_context(session: AsyncSession, user_id: int) -> dict:
    """Build full context for LLM: profile, meals (7 days), totals, history."""
    ctx: dict = {}
    now = dt.datetime.utcnow()

    # ── User profile ──
    user = await session.get(User, user_id)
    if user:
        profile = {}
        for f in ['height_cm', 'weight_kg', 'age', 'gender', 'goal',
                   'target_kcal', 'target_protein_g', 'target_fat_g', 'target_carbs_g']:
            val = getattr(user, f, None)
            if val is not None:
                profile[f] = val
        if profile:
            ctx["profile"] = profile

    # ── Meals (7 days) with today_idx ──
    week_ago = now - dt.timedelta(days=7)
    result = await session.execute(
        select(Meal).where(Meal.user_id == user_id, Meal.eaten_at >= week_ago).order_by(Meal.eaten_at.asc()))
    all_meals = result.scalars().all()

    if all_meals:
        ctx["all_meals"] = []
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_idx = 1
        for m in all_meals:
            entry = _meal_to_dict(m)
            if m.eaten_at >= today_start:
                entry["today_idx"] = today_idx
                today_idx += 1
            ctx["all_meals"].append(entry)

    # ── Today's totals + remaining to goal ──
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_meals = [m for m in all_meals if m.eaten_at >= today_start]
    if today_meals:
        ctx["totals_today"] = _calc_totals(today_meals)
        if user and user.target_kcal:
            ctx["remaining"] = _calc_remaining(today_meals, user)

    # ── Recent messages ──
    result = await session.execute(
        select(RawMessage).where(RawMessage.user_id == user_id)
        .order_by(RawMessage.created_at.desc()).limit(15))
    recent = list(result.scalars()); recent.reverse()
    ctx["history"] = [{"role": "user", "text": rm.text or "(фото)"} for rm in recent]

    return ctx


def _meal_to_dict(m: Meal) -> dict:
    items = [{"name": it.name, "grams": _grams_str(it)} for it in m.items]
    cal = f"{m.calories_min}–{m.calories_max} ккал" if m.calories_min else "?"
    return {
        "id": m.id, "date": m.eaten_at.strftime("%Y-%m-%d"),
        "time": m.eaten_at.strftime("%H:%M"),
        "meal_type": format_meal_type(m.meal_type),
        "items": items, "calories": cal,
        "confidence": m.confidence,
        # Raw values for totals calculation
        "_cal_min": m.calories_min or 0, "_cal_max": m.calories_max or 0,
        "_prot_min": m.protein_min_g or 0, "_fat_min": m.fat_min_g or 0,
        "_carbs_min": m.carbs_min_g or 0,
    }


def _grams_str(item) -> str:
    return f"{item.calories_min or '?'}"


def _calc_totals(meals) -> dict:
    return {
        "calories": f"{sum(m.calories_min or 0 for m in meals)} ккал",
        "protein": f"{sum(m.protein_min_g or 0 for m in meals):.0f} г",
        "fat": f"{sum(m.fat_min_g or 0 for m in meals):.0f} г",
        "carbs": f"{sum(m.carbs_min_g or 0 for m in meals):.0f} г",
    }


def _calc_remaining(meals, user) -> dict:
    t_cal = sum(m.calories_min or 0 for m in meals)
    t_prot = sum(m.protein_min_g or 0 for m in meals)
    t_fat = sum(m.fat_min_g or 0 for m in meals)
    t_carbs = sum(m.carbs_min_g or 0 for m in meals)
    return {
        "kcal": max(0, (user.target_kcal or 2000) - t_cal),
        "protein_g": max(0, (user.target_protein_g or 60) - t_prot),
        "fat_g": max(0, (user.target_fat_g or 50) - t_fat),
        "carbs_g": max(0, (user.target_carbs_g or 200) - t_carbs),
    }
