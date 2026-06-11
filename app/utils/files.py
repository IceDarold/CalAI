"""File handling utilities — photo download, path helpers."""

import os
from datetime import datetime
from pathlib import Path

from app.config import settings


def ensure_photo_dir() -> Path:
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    dir_path = settings.photos_dir / today_str
    os.makedirs(dir_path, exist_ok=True)
    return dir_path


async def download_telegram_photo(bot, file_id: str) -> Path | None:
    """Download a photo using aiogram Bot (respects proxy config).

    Args:
        bot: aiogram Bot instance (uses its session with proxy)
        file_id: Telegram file_id of the photo

    Returns:
        Local path to the downloaded file, or None on failure.
    """
    try:
        tg_file = await bot.get_file(file_id)
        file_bytes = await bot.download_file(tg_file.file_path)

        photo_dir = ensure_photo_dir()
        local_filename = f"{datetime.utcnow().strftime('%H%M%S_%f')}.jpg"
        local_path = photo_dir / local_filename
        local_path.write_bytes(file_bytes.read())
        return local_path
    except Exception:
        return None


async def download_telegram_voice(bot, file_id: str) -> bytes | None:
    """Download a voice message using aiogram Bot (respects proxy config)."""
    try:
        tg_file = await bot.get_file(file_id)
        file_bytes = await bot.download_file(tg_file.file_path)
        return file_bytes.read()
    except Exception:
        return None
