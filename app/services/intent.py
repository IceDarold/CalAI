"""Intent detection service."""

from app.config import settings
from app.providers import get_intent_provider
from app.providers.base import BaseIntentProvider
from app.schemas.intent import IntentResult, IntentType


class IntentDetector:
    """Detects user intent from message text.

    Uses rule-based detection first. Falls back to LLM if configured and rule-based returns unknown.
    """

    def __init__(self) -> None:
        self._provider: BaseIntentProvider = get_intent_provider()

    async def detect(self, text: str | None) -> IntentResult:
        """Detect intent from message text.

        Returns SHOW_TODAY if text is None (photo-only message).
        """
        if text is None:
            return IntentResult(intent=IntentType.LOG_MEAL, confidence=0.5, reasoning="photo with no caption - assume food")

        # Always run rule-based first
        from app.providers.mock import MockProvider
        mock = MockProvider()
        result = await mock.detect_intent(text)

        # If rule-based returned unknown and we have a real LLM provider, try it
        if result.intent == IntentType.UNKNOWN and settings.llm_provider != "mock":
            try:
                llm_result = await self._provider.detect_intent(text)
                if llm_result.confidence > result.confidence:
                    return llm_result
            except Exception:
                pass

        return result
