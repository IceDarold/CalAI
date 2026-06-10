"""Provider factory — creates the appropriate provider based on config."""

from app.config import settings
from app.providers.base import BaseFoodTextProvider, BaseIntentProvider, BaseVisionProvider
from app.providers.mock import MockProvider
from app.providers.text_llm import LLMTextProvider
from app.providers.vision_llm import LLMVisionProvider


def get_text_provider() -> BaseFoodTextProvider:
    """Get the configured food text analysis provider."""
    if settings.llm_provider == "openai_compatible" and settings.llm_api_key:
        return LLMTextProvider()
    return MockProvider()


def get_intent_provider() -> BaseIntentProvider:
    """Get the configured intent detection provider."""
    if settings.llm_provider == "openai_compatible" and settings.llm_api_key:
        return LLMTextProvider()
    return MockProvider()


def get_vision_provider() -> BaseVisionProvider:
    """Get the configured vision analysis provider."""
    if settings.vision_provider == "openai_compatible" and (settings.vision_api_key or settings.llm_api_key):
        return LLMVisionProvider()
    return MockProvider()
