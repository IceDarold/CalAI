"""Intent detection service — AI-first with rule-based fallback."""

import logging

from app.config import settings
from app.providers import get_intent_provider
from app.providers.base import BaseIntentProvider
from app.schemas.intent import IntentResult, IntentType

logger = logging.getLogger(__name__)


class IntentDetector:
    """Detects user intent from message text.

    Strategy:
    - If AI is configured → use AI (YandexGPT / OpenAI)
    - If AI fails or not configured → rule-based fallback
    """

    def __init__(self) -> None:
        self._provider: BaseIntentProvider = get_intent_provider()

    async def detect(self, text: str | None) -> IntentResult:
        """Detect intent from message text."""
        if text is None:
            return IntentResult(intent=IntentType.LOG_MEAL, confidence=0.5, reasoning="photo — assume food")

        # Try AI first if configured
        if settings.is_ai_configured:
            try:
                result = await self._provider.detect_intent(text)
                if result.confidence > 0.5:
                    return result
                logger.info(f"AI intent confidence low ({result.confidence}), falling back to rule-based")
            except Exception as e:
                logger.warning(f"AI intent detection failed: {e}")

        # Rule-based fallback
        from app.providers.mock import MockProvider
        mock = MockProvider()
        return await mock.detect_intent(text)
