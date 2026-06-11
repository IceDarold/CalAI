"""Repository layer — CRUD operations for CalAI entities."""

import datetime
from typing import Sequence

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Meal, MealItem, RawMessage, User


async def get_or_create_user(
    session: AsyncSession,
    telegram_id: int,
    username: str | None = None,
    first_name: str = "",
) -> User:
    """Get existing user by telegram_id, or create a new one."""
    result = await session.execute(
        select(User).where(User.telegram_id == telegram_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        user = User(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
        )
        session.add(user)
        await session.flush()
    else:
        # Update name/username on each interaction
        if username and user.username != username:
            user.username = username
        if first_name and user.first_name != first_name:
            user.first_name = first_name
    return user


async def save_meal(
    session: AsyncSession,
    user_id: int,
    meal_type: str,
    source_type: str,
    original_text: str | None,
    photo_path: str | None,
    calories_min: int | None,
    calories_max: int | None,
    protein_min_g: float | None,
    protein_max_g: float | None,
    confidence: str,
    status: str,
    items_data: list[dict],
    eaten_at: datetime.datetime | None = None,
) -> Meal:
    """Save a meal with its items."""
    meal = Meal(
        user_id=user_id,
        meal_type=meal_type,
        source_type=source_type,
        original_text=original_text,
        photo_path=photo_path,
        calories_min=calories_min,
        calories_max=calories_max,
        protein_min_g=protein_min_g,
        protein_max_g=protein_max_g,
        confidence=confidence,
        status=status,
        eaten_at=eaten_at or datetime.datetime.utcnow(),
    )
    session.add(meal)
    await session.flush()

    for item_data in items_data:
        item = MealItem(
            meal_id=meal.id,
            name=item_data["name"],
            portion_text=item_data.get("portion_text"),
            calories_min=item_data.get("calories_min"),
            calories_max=item_data.get("calories_max"),
            protein_min_g=item_data.get("protein_min_g"),
            protein_max_g=item_data.get("protein_max_g"),
            confidence=item_data.get("confidence", "medium"),
        )
        session.add(item)

    await session.flush()
    # Refresh to load relationships (items) for immediate access
    await session.refresh(meal, attribute_names=["items"])
    return meal


async def save_raw_message(
    session: AsyncSession,
    user_id: int,
    telegram_message_id: int,
    message_type: str,
    text: str | None = None,
    photo_path: str | None = None,
) -> RawMessage:
    """Save a raw incoming message."""
    msg = RawMessage(
        user_id=user_id,
        telegram_message_id=telegram_message_id,
        message_type=message_type,
        text=text,
        photo_path=photo_path,
    )
    session.add(msg)
    await session.flush()
    return msg


async def get_today_meals(
    session: AsyncSession,
    user_id: int,
    tz_offset_hours: int = 0,
) -> Sequence[Meal]:
    """Get all meals for the current day in the user's timezone."""
    now_utc = datetime.datetime.utcnow()
    today_start_utc = now_utc.replace(hour=0, minute=0, second=0, microsecond=0) - datetime.timedelta(hours=tz_offset_hours)
    today_end_utc = today_start_utc + datetime.timedelta(days=1)

    result = await session.execute(
        select(Meal)
        .where(
            and_(
                Meal.user_id == user_id,
                Meal.eaten_at >= today_start_utc,
                Meal.eaten_at < today_end_utc,
            )
        )
        .order_by(Meal.eaten_at.asc())
    )
    return result.scalars().all()


async def get_meals_for_date(
    session: AsyncSession,
    user_id: int,
    date_str: str,  # "YYYY-MM-DD"
    tz_offset_hours: int = 0,
) -> Sequence[Meal]:
    """Get all meals for a specific date."""
    try:
        date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return []

    start_utc = date - datetime.timedelta(hours=tz_offset_hours)
    end_utc = start_utc + datetime.timedelta(days=1)

    result = await session.execute(
        select(Meal)
        .where(Meal.user_id == user_id, Meal.eaten_at >= start_utc, Meal.eaten_at < end_utc)
        .order_by(Meal.eaten_at.asc())
    )
    return result.scalars().all()


async def get_meal_by_id(session: AsyncSession, meal_id: int) -> Meal | None:
    """Get a single meal by its ID."""
    result = await session.execute(select(Meal).where(Meal.id == meal_id))
    return result.scalar_one_or_none()


async def get_totals_for_date(
    session: AsyncSession,
    user_id: int,
    date_str: str,
    tz_offset_hours: int = 0,
) -> dict:
    """Get aggregated totals for a specific date."""
    try:
        date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return {"calories_min": 0, "calories_max": 0, "protein_min_g": 0.0, "protein_max_g": 0.0, "meal_count": 0}

    start_utc = date - datetime.timedelta(hours=tz_offset_hours)
    end_utc = start_utc + datetime.timedelta(days=1)

    result = await session.execute(
        select(
            func.coalesce(func.sum(Meal.calories_min), 0).label("cal_min_total"),
            func.coalesce(func.sum(Meal.calories_max), 0).label("cal_max_total"),
            func.coalesce(func.sum(Meal.protein_min_g), 0.0).label("prot_min_total"),
            func.coalesce(func.sum(Meal.protein_max_g), 0.0).label("prot_max_total"),
            func.count(Meal.id).label("meal_count"),
        )
        .where(Meal.user_id == user_id, Meal.eaten_at >= start_utc, Meal.eaten_at < end_utc)
    )
    row = result.one()
    return {
        "calories_min": int(row.cal_min_total),
        "calories_max": int(row.cal_max_total),
        "protein_min_g": float(row.prot_min_total),
        "protein_max_g": float(row.prot_max_total),
        "meal_count": int(row.meal_count),
    }


async def get_today_totals(
    session: AsyncSession,
    user_id: int,
    tz_offset_hours: int = 0,
) -> dict:
    """Get aggregated totals for today."""
    now_utc = datetime.datetime.utcnow()
    today_start_utc = now_utc.replace(hour=0, minute=0, second=0, microsecond=0) - datetime.timedelta(hours=tz_offset_hours)
    today_end_utc = today_start_utc + datetime.timedelta(days=1)

    result = await session.execute(
        select(
            func.coalesce(func.min(Meal.calories_min), 0).label("cal_min_sum"),
            func.coalesce(func.max(Meal.calories_max), 0).label("cal_max_sum"),
            func.coalesce(func.min(Meal.protein_min_g), 0.0).label("prot_min_sum"),
            func.coalesce(func.max(Meal.protein_max_g), 0.0).label("prot_max_sum"),
            func.count(Meal.id).label("meal_count"),
            func.coalesce(func.sum(Meal.calories_min), 0).label("cal_min_total"),
            func.coalesce(func.sum(Meal.calories_max), 0).label("cal_max_total"),
            func.coalesce(func.sum(Meal.protein_min_g), 0.0).label("prot_min_total"),
            func.coalesce(func.sum(Meal.protein_max_g), 0.0).label("prot_max_total"),
        )
        .where(
            and_(
                Meal.user_id == user_id,
                Meal.eaten_at >= today_start_utc,
                Meal.eaten_at < today_end_utc,
                Meal.status == "confirmed",
            )
        )
    )
    row = result.one()
    return {
        "calories_min": int(row.cal_min_total),
        "calories_max": int(row.cal_max_total),
        "protein_min_g": float(row.prot_min_total),
        "protein_max_g": float(row.prot_max_total),
        "meal_count": int(row.meal_count),
    }
