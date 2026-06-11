"""Speech-to-text providers. Converts voice messages to text."""

import logging
from abc import ABC, abstractmethod

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

YANDEX_STT_URL = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"


class BaseSTTProvider(ABC):
    @abstractmethod
    async def transcribe(self, audio_bytes: bytes, lang: str = "ru-RU") -> str | None:
        ...


class YandexSTTProvider(BaseSTTProvider):
    """Yandex SpeechKit — accepts OGG/OPUS natively, no conversion needed."""

    async def transcribe(self, audio_bytes: bytes, lang: str = "ru-RU") -> str | None:
        if not settings.yandex_api_key or not settings.yandex_folder_id:
            logger.warning("Yandex STT: no API key configured")
            return None

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    YANDEX_STT_URL,
                    headers={"Authorization": f"Api-Key {settings.yandex_api_key}"},
                    params={"folderId": settings.yandex_folder_id, "lang": lang},
                    content=audio_bytes,
                )
                resp.raise_for_status()
                data = resp.json()
                return data.get("result", "")
        except Exception as e:
            logger.error(f"Yandex STT failed: {e}")
            return None


class MockSTTProvider(BaseSTTProvider):
    async def transcribe(self, audio_bytes: bytes, lang: str = "ru-RU") -> str | None:
        return None  # Not configured


def get_stt_provider() -> BaseSTTProvider:
    if settings.yandex_api_key and settings.yandex_folder_id:
        return YandexSTTProvider()
    return MockSTTProvider()
