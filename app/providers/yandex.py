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


FOOD_ANALYSIS_SYSTEM_PROMPT = """Ты — парсер еды. Пользователь описывает, что он съел. Твоя задача — ТОЛЬКО извлечь продукты и примерные граммы. Калории НЕ считай — их посчитает база данных.

Верни СТРОГО JSON:

Правила:
1. Определи is_food (true/false). False только если это точно не еда (например "привет", "как дела", "погода хорошая").
2. Определи meal_type: breakfast/lunch/dinner/snack/unknown (на основе текста и контекста).
3. Выдели отдельные продукты/блюда в items. Для каждого:
   - name_ru: название на русском, как его видит пользователь ("куриная грудка", "рис", "гречка", "яблоко")
   - name_en: название НА АНГЛИЙСКОМ для поиска в базе USDA, максимально простое ("chicken breast cooked", "white rice boiled", "buckwheat groats cooked", "apple raw")
   - grams: примерный вес в граммах (если порция не указана — оцени консервативно: стандартная порция)
   - grams_confidence: "high" если граммы явно указаны, "medium" если можно оценить, "low" если непонятно
   - portion_text: кратко почему такой вес ("указано 150 г", "стандартная порция", "примерно 1 тарелка")
4. Определи общую confidence:
   - high: все продукты и порции ясны
   - medium: есть примерное понимание
   - low: много неясного
5. Если confidence low или какой-то продукт непонятен — добавь уточняющие вопросы в questions
6. НЕ давай медицинских советов
7. НЕ считай калории/белки/жиры/углеводы — это сделает база данных
8. ВСЕГДА возвращай валидный JSON, без текста до или после

Пример ответа:
{
  "is_food": true,
  "meal_type": "lunch",
  "items": [
    {
      "name_ru": "куриная грудка",
      "name_en": "chicken breast cooked",
      "grams": 150,
      "grams_confidence": "medium",
      "portion_text": "стандартная порция"
    },
    {
      "name_ru": "рис",
      "name_en": "white rice boiled",
      "grams": 200,
      "grams_confidence": "low",
      "portion_text": "примерно 1 тарелка"
    }
  ],
  "confidence": "medium",
  "questions": ["Сколько примерно грамм риса было?"]
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

    def _parse_raw_json(self, raw: str) -> dict:
        """Extract JSON from potentially markdown-wrapped LLM response."""
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines)
        return json.loads(text)

    def _parse_analysis(self, raw: str) -> FoodAnalysis:
        """Parse YandexGPT response into FoodAnalysis.

        Note: calories/protein are NOT in the LLM response anymore —
        they come from USDA lookup + calculator in food_analyzer.py.
        This method extracts parsed items (name + grams) and metadata.
        """
        data = self._parse_raw_json(raw)

        items = []
        for item_data in data.get("items", []):
            items.append(FoodItem(
                name=item_data.get("name", ""),
                portion_text=item_data.get("portion_text"),
                calories_min=None,  # filled by calculator
                calories_max=None,   # filled by calculator
                protein_min_g=None,  # filled by calculator
                protein_max_g=None,  # filled by calculator
                confidence=Confidence(item_data.get("grams_confidence", "medium")),
            ))

        from app.schemas.food import ParsedFoodItem

        return FoodAnalysis(
            is_food=data.get("is_food", True),
            meal_type=MealType(data.get("meal_type", "unknown")),
            items=items,
            total_calories_min=None,   # filled by calculator
            total_calories_max=None,    # filled by calculator
            total_protein_min_g=None,   # filled by calculator
            total_protein_max_g=None,   # filled by calculator
            confidence=Confidence(data.get("confidence", "medium")),
            questions=data.get("questions", []),
            raw_response=raw,
            parsed_items=[
                ParsedFoodItem(
                    name_ru=item_data.get("name_ru", item_data.get("name", "")),
                    name_en=item_data.get("name_en", item_data.get("name", "")),
                    grams=float(item_data.get("grams", 100)),
                    grams_confidence=item_data.get("grams_confidence", "medium"),
                    portion_text=item_data.get("portion_text", ""),
                )
                for item_data in data.get("items", [])
            ],
        )
