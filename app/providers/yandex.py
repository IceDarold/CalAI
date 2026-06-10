"""YandexGPT provider — native Yandex Foundation Models API.

Uses the Yandex Cloud LLM API directly (not OpenAI-compatible).
Reference: https://yandex.cloud/ru/docs/foundation-models/concepts/api
"""

import json
import logging

import httpx

from app.config import settings
from app.providers.base import BaseFoodTextProvider, BaseIntentProvider
from app.schemas.food import Confidence, FoodAnalysis, FoodItem, MealType
from app.schemas.intent import IntentResult, IntentType

logger = logging.getLogger(__name__)

YANDEX_API_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"


def _model_uri() -> str:
    return f"gpt://{settings.yandex_folder_id}/{settings.yandex_model}"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Api-Key {settings.yandex_api_key}",
        "Content-Type": "application/json",
    }


INTENT_SYSTEM_PROMPT = """Ты — классификатор интентов для Telegram-бота трекера питания.
Пользователь пишет в свободной форме. Определи, что он хочет.

Интенты:
- log_meal: пользователь рассказывает, что съел (конкретные продукты или блюда)
- show_today: хочет посмотреть итоги за сегодня
- help: просит помощь, не понимает как пользоваться
- unknown: не относится к трекингу еды (привет, погода, как дела и т.д.)

Ответ верни СТРОГО в JSON:
{"intent": "...", "confidence": 0.0-1.0, "reasoning": "..."}

Примеры:
"съел курицу с рисом" → {"intent": "log_meal", "confidence": 0.95, "reasoning": "описание приёма пищи"}
"что я сегодня ел" → {"intent": "show_today", "confidence": 0.95, "reasoning": "запрос итогов дня"}
"как пользоваться" → {"intent": "help", "confidence": 0.9, "reasoning": "просьба помощи"}
"привет" → {"intent": "unknown", "confidence": 0.9, "reasoning": "приветствие, не про еду"}
"на обед была гречка с котлетой" → {"intent": "log_meal", "confidence": 0.95, "reasoning": "описание обеда"}
"сохрани это как перекус" → {"intent": "log_meal", "confidence": 0.9, "reasoning": "просьба сохранить перекус"}"""


FOOD_ANALYSIS_SYSTEM_PROMPT = """Ты — эксперт-нутрициолог. Пользователь описывает, что он съел. Твоя задача — проанализировать и вернуть СТРОГО JSON.

Правила:
1. Определи is_food (true/false). False только если это точно не еда.
2. Определи meal_type: breakfast/lunch/dinner/snack/unknown (на основе текста и контекста)
3. Выдели отдельные продукты/блюда в items. Для каждого:
   - name: название на русском
   - portion_text: примерный размер порции (если можно оценить из текста)
   - calories_min, calories_max: диапазон ккал (если непонятна порция — используй консервативную оценку)
   - protein_min_g, protein_max_g: диапазон белка в граммах
   - confidence: low/medium/high
4. Посчитай total_calories_min/max и total_protein_min_g/max_g
5. Определи общую confidence:
   - high: порции и состав ясны
   - medium: есть примерное понимание
   - low: много неясного
6. Если confidence low — добавь 1-2 уточняющих вопроса в questions
7. НЕ давай медицинских советов и рекомендаций по питанию
8. НЕ используй точные цифры там, где их нет — ТОЛЬКО диапазоны
9. ВСЕГДА возвращай валидный JSON, без текста до или после

Пример ответа:
{
  "is_food": true,
  "meal_type": "lunch",
  "items": [
    {
      "name": "куриная грудка",
      "portion_text": "~150-200 г",
      "calories_min": 250,
      "calories_max": 350,
      "protein_min_g": 35,
      "protein_max_g": 50,
      "confidence": "medium"
    },
    {
      "name": "рис отварной",
      "portion_text": "~150 г",
      "calories_min": 170,
      "calories_max": 210,
      "protein_min_g": 3,
      "protein_max_g": 5,
      "confidence": "medium"
    }
  ],
  "total_calories_min": 420,
  "total_calories_max": 560,
  "total_protein_min_g": 38,
  "total_protein_max_g": 55,
  "confidence": "medium",
  "questions": []
}"""


class YandexGPTProvider(BaseFoodTextProvider, BaseIntentProvider):
    """YandexGPT provider for text food analysis and intent detection.

    Uses Yandex Foundation Models API (non-OpenAI format).
    """

    async def detect_intent(self, text: str) -> IntentResult:
        """Detect user intent via YandexGPT."""
        try:
            raw = await self._call_api(INTENT_SYSTEM_PROMPT, text)
            data = json.loads(raw)
            return IntentResult(
                intent=IntentType(data.get("intent", "unknown")),
                confidence=float(data.get("confidence", 0.5)),
                reasoning=data.get("reasoning", ""),
            )
        except Exception as e:
            logger.warning(f"YandexGPT intent detection failed: {e}")
            return IntentResult(intent=IntentType.UNKNOWN, confidence=0.0, reasoning=f"API error: {e}")

    async def analyze_food_text(
        self, text: str, context: dict | None = None
    ) -> FoodAnalysis:
        """Analyze food from text via YandexGPT."""
        try:
            raw = await self._call_api(FOOD_ANALYSIS_SYSTEM_PROMPT, text)
            return self._parse_analysis(raw)
        except Exception as e:
            logger.error(f"YandexGPT food analysis failed: {e}")
            return FoodAnalysis(
                is_food=False,
                meal_type=MealType.UNKNOWN,
                confidence=Confidence.LOW,
                questions=["Не удалось проанализировать через AI. Попробуй ещё раз или опиши по-другому."],
            )

    async def _call_api(self, system_prompt: str, user_message: str) -> str:
        """Call YandexGPT completion API. Returns raw text response."""
        body = {
            "modelUri": _model_uri(),
            "completionOptions": {
                "temperature": 0.3,
                "maxTokens": "4000",
                "responseFormat": {"type": "json_object"},
            },
            "messages": [
                {"role": "system", "text": system_prompt},
                {"role": "user", "text": user_message},
            ],
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(YANDEX_API_URL, headers=_headers(), json=body)
            resp.raise_for_status()
            data = resp.json()

        text = data.get("result", {}).get("alternatives", [{}])[0].get("message", {}).get("text", "")
        if not text:
            raise RuntimeError("Empty response from YandexGPT")
        return text

    def _parse_analysis(self, raw: str) -> FoodAnalysis:
        """Parse YandexGPT JSON response into FoodAnalysis."""
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines)

        data = json.loads(text)

        items = []
        for item_data in data.get("items", []):
            items.append(FoodItem(
                name=item_data.get("name", ""),
                portion_text=item_data.get("portion_text"),
                calories_min=item_data.get("calories_min"),
                calories_max=item_data.get("calories_max"),
                protein_min_g=item_data.get("protein_min_g"),
                protein_max_g=item_data.get("protein_max_g"),
                confidence=Confidence(item_data.get("confidence", "medium")),
            ))

        return FoodAnalysis(
            is_food=data.get("is_food", True),
            meal_type=MealType(data.get("meal_type", "unknown")),
            items=items,
            total_calories_min=data.get("total_calories_min"),
            total_calories_max=data.get("total_calories_max"),
            total_protein_min_g=data.get("total_protein_min_g"),
            total_protein_max_g=data.get("total_protein_max_g"),
            confidence=Confidence(data.get("confidence", "medium")),
            questions=data.get("questions", []),
            raw_response=raw,
        )
