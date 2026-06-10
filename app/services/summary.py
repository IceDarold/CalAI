"""Summary service — builds /today response."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories import get_today_meals, get_today_totals
from app.utils.time import format_meal_type


class SummaryService:
    """Builds daily summary for the user."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_today_summary(self, user_id: int) -> str:
        """Get human-readable summary for today's meals."""
        meals = await get_today_meals(self._session, user_id)

        if not meals:
            return "Сегодня ты ещё ничего не записал. Напиши, что съел, и я посчитаю!"

        confirmed = [m for m in meals if m.status == "confirmed"]
        drafts = [m for m in meals if m.status == "draft"]

        lines = ["Сегодня:\n"]

        for i, meal in enumerate(meals, 1):
            meal_type_str = format_meal_type(meal.meal_type)
            items = meal.items
            items_str = ", ".join(item.name for item in items) if items else "—"

            cal_range = ""
            if meal.calories_min and meal.calories_max:
                cal_range = f" — {meal.calories_min}–{meal.calories_max} ккал"
                if meal.protein_min_g is not None and meal.protein_max_g is not None:
                    cal_range += f", белок {meal.protein_min_g:.0f}–{meal.protein_max_g:.0f} г"

            draft_marker = " ⚠️ (draft)" if meal.status == "draft" else ""
            low_conf_marker = " 🔍" if meal.confidence == "low" else ""

            lines.append(f"{i}. {meal_type_str.capitalize()}{draft_marker}{low_conf_marker} — {items_str}{cal_range}")

        # Totals for confirmed meals
        if confirmed:
            totals = await get_today_totals(self._session, user_id)
            lines.append(
                f"\nИтого: {totals['calories_min']}–{totals['calories_max']} ккал, "
                f"белок {totals['protein_min_g']:.0f}–{totals['protein_max_g']:.0f} г."
            )

        if drafts:
            lines.append(f"\n⚠️ {len(drafts)} записей требуют уточнения (низкая уверенность).")

        return "\n".join(lines)
