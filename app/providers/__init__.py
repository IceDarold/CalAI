"""Provider factory. Priority: GigaChat > Yandex > OpenAI > Mock."""

from app.config import settings
from app.providers.base import BaseFoodTextProvider, BaseIntentProvider, BaseVisionProvider
from app.providers.mock import MockProvider


def get_text_provider() -> BaseFoodTextProvider:
    if settings.ai_provider == "gigachat" and settings.gigachat_credentials:
        from app.providers.gigachat import GigaChatProvider
        return GigaChatProvider()
    if settings.ai_provider == "yandex" and settings.yandex_api_key:
        from app.providers.yandex import YandexGPTProvider
        return YandexGPTProvider()
    if settings.ai_provider == "openai" and settings.openai_api_key:
        from app.providers.text_llm import LLMTextProvider
        return LLMTextProvider()
    return MockProvider()


def get_intent_provider() -> BaseIntentProvider:
    return get_text_provider()  # same provider handles both


def get_vision_provider() -> BaseVisionProvider:
    if settings.ai_provider == "gigachat" and settings.gigachat_credentials:
        from app.providers.gigachat import GigaChatProvider
        return GigaChatProvider()
    if settings.ai_provider == "openai" and settings.openai_api_key:
        from app.providers.vision_llm import LLMVisionProvider
        return LLMVisionProvider()
    return MockProvider()
