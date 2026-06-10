"""Provider factory — creates the appropriate provider based on config.

Priority: YandexGPT > OpenAI > Mock (no-AI fallback)
"""

from app.config import settings
from app.providers.base import BaseFoodTextProvider, BaseIntentProvider, BaseVisionProvider
from app.providers.mock import MockProvider
from app.providers.yandex import YandexGPTProvider
from app.providers.text_llm import LLMTextProvider
from app.providers.vision_llm import LLMVisionProvider


def get_text_provider() -> BaseFoodTextProvider:
    """Get the configured food text analysis provider."""
    if settings.ai_provider == "yandex" and settings.yandex_api_key:
        return YandexGPTProvider()
    if settings.ai_provider == "openai" and settings.openai_api_key:
        return LLMTextProvider()
    return MockProvider()


def get_intent_provider() -> BaseIntentProvider:
    """Get the configured intent detection provider."""
    if settings.ai_provider == "yandex" and settings.yandex_api_key:
        return YandexGPTProvider()
    if settings.ai_provider == "openai" and settings.openai_api_key:
        return LLMTextProvider()
    return MockProvider()


def get_vision_provider() -> BaseVisionProvider:
    """Get the configured vision analysis provider.

    Note: YandexGPT does not support vision natively.
    OpenAI-compatible providers with vision models work when configured.
    """
    if settings.ai_provider == "openai" and settings.openai_api_key:
        return LLMVisionProvider()
    return MockProvider()
