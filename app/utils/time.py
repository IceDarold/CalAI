"""Timezone and date/time utilities."""

import datetime


def utc_now() -> datetime.datetime:
    return datetime.datetime.utcnow()


def get_today_range(tz_offset_hours: int = 0) -> tuple[datetime.datetime, datetime.datetime]:
    """Return (start, end) UTC datetime range for 'today' given a timezone offset."""
    now = utc_now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0) - datetime.timedelta(hours=tz_offset_hours)
    today_end = today_start + datetime.timedelta(days=1)
    return today_start, today_end


def format_meal_type(meal_type: str) -> str:
    """Format meal type in Russian."""
    mapping = {
        "breakfast": "завтрак",
        "lunch": "обед",
        "dinner": "ужин",
        "snack": "перекус",
        "unknown": "приём пищи",
    }
    return mapping.get(meal_type, meal_type)
