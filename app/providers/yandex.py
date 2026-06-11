"""YandexGPT provider — native Yandex Foundation Models API.

Single unified orchestrator: one LLM call handles intent, parsing, and response.
Nutrition numbers come from USDA database + calculator — never from LLM.
"""

import datetime as dt
import json
import logging

import httpx

from app.config import settings
from app.providers.base import BaseFoodTextProvider, BaseIntentProvider
from app.schemas.food import Confidence, FoodAnalysis, FoodItem, MealType, ParsedFoodItem
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


ORCHESTRATOR_SYSTEM_PROMPT = """Ты — дружелюбный трекер питания в Telegram. Ты помогаешь пользователю записывать приёмы пищи.

Ты видишь историю переписки и контекст — ВСЕ приёмы пищи с датами и временем. Используй это чтобы принимать правильные решения.

## Твоя задача
Прочитай сообщение пользователя и контекст. Реши, что нужно сделать, и верни СТРОГО JSON.

## Доступные действия (action)
- "log_meal" — записать новый приём пищи (сегодня, сейчас)
- "append_meal" — добавить продукты к последнему приёму
- "update_meal" — исправить/уточнить последний приём
- "update_meal_by_id" — исправить конкретный приём по ID (когда пользователь говорит "вчера в обед я ел не курицу а индейку")
- "show_today" — показать итоги за сегодня
- "show_date" — показать итоги за конкретную дату (пользователь: "что я ел вчера?", "позавчерашний обед")
- "delete_last" — удалить последнюю запись
- "help" — показать справку
- "unknown" — сообщение не про еду

## Разбор времени приёма пищи (ВАЖНО!)
Пользователь может не указывать время. Тогда действуй так:

1. Если пользователь просто описывает еду ("съел яблоко") И в истории нет уточнений:
   → спроси "Это сейчас съел или раньше?" (НЕ логируй сразу!)

2. Если пользователь уже ответил на вопрос "когда" или указал время:
   - "20 минут назад" → посчитай eaten_at_iso как UTC сейчас минус 20 минут
   - "час назад" → UTC сейчас минус 1 час
   - "в 14:20" → сегодня в 14:20 по местному времени
   - "вчера в обед" → вчера ~13:00
   - "позавчера на ужин" → позавчера ~19:00
   - "где-то в 2 часа дня" → сегодня 14:00
   - "утром" → сегодня ~08:00
   - "в обед" → сегодня ~13:00
   - "вечером" → сегодня ~19:00

   Возвращай eaten_at в формате: "YYYY-MM-DDTHH:MM" (UTC)

3. Если пользователь описывает еду БЕЗ времени и ты НЕ уверен что это прямо сейчас:
   → задай вопрос о времени в response_text, НЕ заполняй eaten_at_iso

4. Если контекст ясно показывает что это сейчас (только что было обсуждение этой еды):
   → eaten_at_iso = текущее UTC время, не спрашивай

## Правила для items
Для каждого продукта:
- name_ru: название на русском
- name_en: название на английском для поиска в базе USDA
- grams: примерный вес в граммах
- grams_confidence: "high" / "medium" / "low"
- portion_text: почему такой вес

## Правила для response_text
- Пиши на русском, дружелюбно, коротко
- НЕ пиши конкретные калории — используй {{CALORIES}} и {{PROTEIN}}
- Если порции неизвестны — используй {{RANGE_NOTE}}
- Если нужно уточнить время — спроси в response_text
- Если нужно уточнить порцию — спроси в response_text

## Стиль
- "Записал как обед: гречка, куриная грудка. {{CALORIES}} {{RANGE_NOTE}}"
- "Добавил к обеду: яблоко. {{CALORIES}}"
- "Это сейчас съел или раньше? Если раньше — когда примерно?"
- "Понял, исправил: рис 200 г. {{CALORIES}}"
- "Вчера у тебя было: ..."

## Примеры

Сообщение: "съел гречку с курицей"
Контекст: первое сообщение, нет истории
Ответ:
{
  "action": "log_meal",
  "meal_type": "lunch",
  "items": [
    {"name_ru": "гречка", "name_en": "buckwheat groats cooked", "grams": 200, "grams_confidence": "medium", "portion_text": "стандартная порция"},
    {"name_ru": "куриная грудка", "name_en": "chicken breast cooked", "grams": 150, "grams_confidence": "medium", "portion_text": "стандартная порция"}
  ],
  "confidence": "medium",
  "eaten_at_iso": null,
  "response_text": "Записал как обед: гречка, куриная грудка. {{CALORIES}} {{RANGE_NOTE}}"
}

Сообщение: "20 минут назад съел банан"
Контекст: любой
Ответ:
{
  "action": "log_meal",
  "meal_type": "snack",
  "items": [
    {"name_ru": "банан", "name_en": "banana raw", "grams": 120, "grams_confidence": "medium", "portion_text": "один банан"}
  ],
  "confidence": "high",
  "eaten_at_iso": "2026-06-10T14:10",
  "response_text": "Записал как перекус: банан. {{CALORIES}}"
}

Сообщение: "что я ел вчера"
Контекст: любой
Ответ:
{
  "action": "show_date",
  "date": "2026-06-09",
  "items": [],
  "confidence": "high",
  "response_text": ""
}

Сообщение: "вчера в обед вместо курицы была индейка"
Контекст: вчерашний обед с ID 42
Ответ:
{
  "action": "update_meal_by_id",
  "meal_id": 42,
  "meal_type": "lunch",
  "items": [
    {"name_ru": "индейка", "name_en": "turkey breast cooked", "grams": 150, "grams_confidence": "medium", "portion_text": "стандартная порция"}
  ],
  "confidence": "medium",
  "response_text": "Исправил вчерашний обед: индейка вместо курицы. {{CALORIES}}"
}"""


class YandexGPTProvider(BaseFoodTextProvider, BaseIntentProvider):
    """YandexGPT provider with unified orchestration.

    One LLM call replaces: intent detection + food parsing + response formatting.
    Only nutrition calculation is done deterministically (USDA + calculator).
    """

    # ── Legacy interface (kept for compatibility) ────────────────────────

    async def detect_intent(self, text: str) -> IntentResult:
        """Legacy intent detection — used as fallback."""
        try:
            raw = await self._call_api(ORCHESTRATOR_SYSTEM_PROMPT, text, json_mode=False)
            data = self._parse_raw_json(raw)
            return IntentResult(
                intent=IntentType(data.get("action", "unknown")),
                confidence=float(data.get("confidence", 0.5)),
                reasoning=data.get("response_text", ""),
            )
        except Exception as e:
            logger.warning(f"YandexGPT intent failed: {e}")
            return IntentResult(intent=IntentType.UNKNOWN, confidence=0.0, reasoning=str(e))

    async def analyze_food_text(
        self, text: str, context: dict | None = None
    ) -> FoodAnalysis:
        """Legacy food analysis — used as fallback."""
        result = await self.orchestrate(text, context)
        items = []
        for item_data in result.get("items", []):
            items.append(FoodItem(
                name=item_data.get("name_ru", item_data.get("name", "")),
                portion_text=item_data.get("portion_text"),
                confidence=Confidence(item_data.get("grams_confidence", "medium")),
            ))
        return FoodAnalysis(
            is_food=result.get("action") in ("log_meal", "append_meal"),
            meal_type=MealType(result.get("meal_type", "unknown")),
            items=items,
            confidence=Confidence(result.get("confidence", "medium")),
            questions=[],
            raw_response=json.dumps(result, ensure_ascii=False),
            parsed_items=[
                ParsedFoodItem(
                    name_ru=it.get("name_ru", it.get("name", "")),
                    name_en=it.get("name_en", it.get("name", "")),
                    grams=float(it.get("grams", 100)),
                    grams_confidence=it.get("grams_confidence", "medium"),
                    portion_text=it.get("portion_text", ""),
                )
                for it in result.get("items", [])
            ],
        )

    # ── Unified orchestrator ─────────────────────────────────────────────

    async def orchestrate(
        self, text: str, context: dict | None = None
    ) -> dict:
        """Single LLM call that decides everything.

        Context: {all_meals, totals_today, history}
        Each meal: {id, date, time, meal_type, items, calories, confidence}
        """
        parts = [f"Сообщение пользователя: {text}", ""]

        if context:
            # All meals from last 7 days
            if context.get("all_meals"):
                parts.append("Все приёмы пищи (последние 7 дней):")
                current_date = None
                for m in context["all_meals"]:
                    if m["date"] != current_date:
                        current_date = m["date"]
                        parts.append(f"  📅 {current_date}:")
                    items = ", ".join(f"{it['name']} ({it['grams']})" for it in m.get("items", []))
                    parts.append(f"    [{m['time']}] #{m['id']} {m['meal_type']}: {items} — {m.get('calories', '?')}")
                parts.append("")

            # Today's totals
            if context.get("totals_today"):
                t = context["totals_today"]
                parts.append(f"Итого сегодня: {t['calories']}, белок {t['protein']}")
                parts.append("")

            # History
            if context.get("history"):
                parts.append("История переписки:")
                for msg in context["history"]:
                    parts.append(f"  пользователь: {msg['text']}")
                parts.append("")
        else:
            parts.append("Контекст: это первое сообщение, истории пока нет.")
            parts.append("")

        parts.append(f"Текущее время (UTC): {dt.datetime.utcnow().strftime('%Y-%m-%dT%H:%M')}")
        parts.append("")

        user_message = "\n".join(parts)

        try:
            raw = await self._call_api(ORCHESTRATOR_SYSTEM_PROMPT, user_message, json_mode=True)
            return self._parse_raw_json(raw)
        except Exception as e:
            logger.error(f"Orchestrator failed: {e}")
            return {
                "action": "unknown",
                "items": [],
                "confidence": "low",
                "response_text": "",
                "_error": str(e),
            }

    # ── Helpers ──────────────────────────────────────────────────────────

    async def _call_api(
        self, system_prompt: str, user_message: str, json_mode: bool = True
    ) -> str:
        body = {
            "modelUri": _model_uri(),
            "completionOptions": {
                "temperature": 0.3,
                "maxTokens": "4000",
            },
            "messages": [
                {"role": "system", "text": system_prompt},
                {"role": "user", "text": user_message},
            ],
        }
        if json_mode:
            body["completionOptions"]["responseFormat"] = {"type": "json_object"}

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(YANDEX_API_URL, headers=_headers(), json=body)
            resp.raise_for_status()
            data = resp.json()

        text = data.get("result", {}).get("alternatives", [{}])[0].get("message", {}).get("text", "")
        if not text:
            raise RuntimeError("Empty response from YandexGPT")
        return text

    def _parse_raw_json(self, raw: str) -> dict:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines)
        return json.loads(text)
