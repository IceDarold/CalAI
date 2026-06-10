"""LLM text provider — calls OpenAI-compatible chat API for food text analysis."""

import json

import httpx

from app.config import settings
from app.providers.base import BaseFoodTextProvider, BaseIntentProvider
from app.schemas.food import Confidence, FoodAnalysis, FoodItem, MealType
from app.schemas.intent import IntentResult, IntentType


FOOD_TEXT_SYSTEM_PROMPT = """Ты — ассистент для трекинга питания. Пользователь описывает, что он съел.
Твоя задача — проанализировать описание и вернуть ТОЛЬКО JSON.

Правила:
1. Если это НЕ описание еды — is_food: false
2. Определи meal_type: breakfast/lunch/dinner/snack/unknown
3. Выдели отдельные продукты/блюда как items
4. Для каждого item укажи:
   - name (на русском, понятно)
   - portion_text (примерный размер порции, если можно понять из текста)
   - calories_min, calories_max (диапазон, если порция неизвестна — используй консервативную оценку)
   - protein_min_g, protein_max_g
   - confidence: low/medium/high
5. Посчитай total_calories_min/max, total_protein_min_g/max_g
6. Определи общую confidence
7. Если данных мало или порции неясны — добавь уточняющие вопросы в questions
8. НЕ давай медицинских советов
9. НЕ используй точные цифры там, где их нет — только диапазоны

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
    }
  ],
  "total_calories_min": 500,
  "total_calories_max": 700,
  "total_protein_min_g": 35,
  "total_protein_max_g": 55,
  "confidence": "medium",
  "questions": []
}"""

INTENT_SYSTEM_PROMPT = """Ты — классификатор интентов для бота-трекера питания.
Определи, что хочет пользователь. Верни ТОЛЬКО JSON.

Интенты:
- log_meal: пользователь рассказывает, что съел
- show_today: хочет посмотреть, что съел сегодня
- help: просит помощь или не понимает, как пользоваться
- unknown: непонятное сообщение

Примеры:
"съел курицу с рисом" → log_meal
"что я сегодня ел" → show_today
"как это работает" → help
"привет" → unknown

Ответ в формате:
{"intent": "log_meal", "confidence": 0.95, "reasoning": "описывает приём пищи"}"""


class LLMTextProvider(BaseFoodTextProvider, BaseIntentProvider):
    """OpenAI-compatible chat API provider for text analysis and intent detection."""

    def __init__(self) -> None:
        self.api_key = settings.llm_api_key
        self.base_url = settings.llm_base_url.rstrip("/")
        self.model = settings.llm_model

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def detect_intent(self, text: str) -> IntentResult:
        """Use LLM to detect intent."""
        try:
            response = await self._chat_completion(
                system_prompt=INTENT_SYSTEM_PROMPT,
                user_message=text,
            )
            data = json.loads(response)
            return IntentResult(
                intent=IntentType(data.get("intent", "unknown")),
                confidence=float(data.get("confidence", 0.5)),
                reasoning=data.get("reasoning", ""),
            )
        except Exception:
            return IntentResult(intent=IntentType.UNKNOWN, confidence=0.0, reasoning="LLM call failed")

    async def analyze_food_text(
        self, text: str, context: dict | None = None
    ) -> FoodAnalysis:
        """Use LLM to analyze food from text."""
        try:
            response = await self._chat_completion(
                system_prompt=FOOD_TEXT_SYSTEM_PROMPT,
                user_message=text,
            )
            return self._parse_food_analysis(response)
        except Exception:
            return FoodAnalysis(
                is_food=False,
                meal_type=MealType.UNKNOWN,
                confidence=Confidence.LOW,
                questions=["Не удалось проанализировать. Попробуй ещё раз или опиши по-другому."],
            )

    async def _chat_completion(
        self, system_prompt: str, user_message: str
    ) -> str:
        """Make a chat completion API call."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers,
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 1000,
                },
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]

    def _parse_food_analysis(self, raw_json: str) -> FoodAnalysis:
        """Parse LLM JSON response into FoodAnalysis."""
        # Try to extract JSON from potential markdown code blocks
        text = raw_json.strip()
        if text.startswith("```"):
            # Remove code block markers
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
            raw_response=raw_json,
        )
