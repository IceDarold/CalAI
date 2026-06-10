"""File handling utilities — photo download, path helpers."""

import os
from datetime import datetime
from pathlib import Path

import httpx

from app.config import settings


def ensure_photo_dir() -> Path:
    """Ensure the photos directory exists and return today's subdirectory."""
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    dir_path = settings.photos_dir / today_str
    os.makedirs(dir_path, exist_ok=True)
    return dir_path


async def download_telegram_photo(
    file_path: str,
    bot_token: str,
) -> Path | None:
    """Download a photo from Telegram servers to local storage.

    Args:
        file_path: Telegram file_path from getFile()
        bot_token: Bot token for API access

    Returns:
        Local path to the downloaded file, or None on failure.
    """
    url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
    photo_dir = ensure_photo_dir()
    local_filename = f"{datetime.utcnow().strftime('%H%M%S_%f')}.jpg"
    local_path = photo_dir / local_filename

    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        if response.status_code == 200:
            local_path.write_bytes(response.content)
            return local_path
    return None
