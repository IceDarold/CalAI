"""LLM vision provider — calls OpenAI-compatible vision API for food photo analysis."""

import base64
import json
from pathlib import Path

import httpx

from app.config import settings
from app.providers.base import BaseVisionProvider
from app.schemas.food import Confidence, FoodAnalysis, FoodItem, MealType


VISION_SYSTEM_PROMPT = """Ты — ассистент для трекинга питания. Пользователь прислал фото еды.
Твоя задача — проанализировать, что на фото, и вернуть ТОЛЬКО JSON.

Правила:
1. Определи, еда ли это вообще (is_food: true/false)
2. Если не еда — is_food: false, остальное не важно
3. Определи meal_type: breakfast/lunch/dinner/snack/unknown
4. Выдели отдельные блюда/продукты как items
5. Для каждого item:
   - name (на русском)
   - portion_text (визуальная оценка: "около 150-200 г", "1 тарелка", "2 шт")
   - calories_min, calories_max (диапазон)
   - protein_min_g, protein_max_g
   - confidence: low/medium/high
6. Посчитай общие total_calories_min/max, total_protein_min_g/max_g
7. Определи общую confidence (если плохо видно — low)
8. Если что-то неясно — добавь вопросы в questions
9. НЕ давай медицинских советов
10. Используй только диапазоны, не точные цифры"""


class LLMVisionProvider(BaseVisionProvider):
    """OpenAI-compatible vision API provider for food photo analysis."""

    def __init__(self) -> None:
        api_key = settings.vision_api_key or settings.llm_api_key
        base_url = (settings.vision_base_url or settings.llm_base_url).rstrip("/")
        model = settings.vision_model or settings.llm_model
        self.api_key = api_key
        self.base_url = base_url
        self.model = model

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def analyze_food_photo(
        self, photo_path: str, caption: str | None = None
    ) -> FoodAnalysis:
        """Analyze food from photo using vision API."""
        photo_path_obj = Path(photo_path)
        if not photo_path_obj.exists():
            return FoodAnalysis(
                is_food=False,
                meal_type=MealType.UNKNOWN,
                confidence=Confidence.LOW,
                questions=["Не могу найти фото. Отправь ещё раз, пожалуйста."],
            )

        try:
            # Read and encode image
            image_bytes = photo_path_obj.read_bytes()
            image_b64 = base64.b64encode(image_bytes).decode("utf-8")

            user_content: list[dict] = [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                },
                {
                    "type": "text",
                    "text": caption or "Что на этом фото? Это еда? Проанализируй и верни JSON.",
                },
            ]

            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers,
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": VISION_SYSTEM_PROMPT},
                            {"role": "user", "content": user_content},
                        ],
                        "temperature": 0.3,
                        "max_tokens": 1000,
                    },
                )
                response.raise_for_status()
                data = response.json()
                raw_content = data["choices"][0]["message"]["content"]

            return self._parse_food_analysis(raw_content)
        except Exception:
            return FoodAnalysis(
                is_food=False,
                meal_type=MealType.UNKNOWN,
                confidence=Confidence.LOW,
                questions=["Не удалось проанализировать фото. Опиши, что там было, текстом."],
            )

    def _parse_food_analysis(self, raw_json: str) -> FoodAnalysis:
        """Parse LLM JSON response into FoodAnalysis."""
        text = raw_json.strip()
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
            raw_response=raw_json,
        )
