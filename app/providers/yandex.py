"""YandexGPT provider — native Yandex Foundation Models API.

Single unified orchestrator: one LLM call handles intent, parsing, and response.
Nutrition numbers come from USDA database + calculator — never from LLM.
"""

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

Ты видишь историю переписки и контекст — все приёмы пищи за сегодня, последний приём, черновики. Используй это чтобы принимать правильные решения.

## Твоя задача
Прочитай сообщение пользователя и контекст. Реши, что нужно сделать, и верни СТРОГО JSON.

## Доступные действия (action)
- "log_meal" — записать новый приём пищи
- "append_meal" — добавить продукты к последнему приёму (пользователь написал "и ещё", "плюс", "добавь" и т.д.)
- "update_meal" — исправить/уточнить последний приём (пользователь говорит "нет, там было 200 грамм", "вместо риса была гречка", "на самом деле это был ужин")
- "show_today" — показать итоги за сегодня
- "delete_last" — удалить последнюю запись
- "help" — показать справку
- "unknown" — сообщение не про еду

## Правила для items (когда action = log_meal, append_meal или update_meal)
Для каждого продукта:
- name_ru: название на русском ("куриная грудка", "гречка", "яблоко")
- name_en: название на английском для поиска в базе ("chicken breast cooked", "buckwheat groats", "apple raw")
- grams: примерный вес в граммах (если не указан — стандартная порция)
- grams_confidence: "high" / "medium" / "low"
- portion_text: почему такой вес ("указано 150 г", "стандартная порция")

## Когда какое действие выбирать
- "update_meal" — пользователь УТОЧНЯЕТ или ИСПРАВЛЯЕТ что-то в последнем приёме. Примеры:
  - "нет, там было 200 грамм риса" → заменить рис на 200г
  - "вместо курицы была индейка" → заменить продукт
  - "на самом деле это был ужин" → сменить meal_type
  - "без масла" → убрать масло из записи
- "append_meal" — пользователь ДОБАВЛЯЕТ новые продукты к тому же приёму
- "log_meal" — новый самостоятельный приём пищи

## Правила для response_text
- Пиши на русском, дружелюбно, коротко
- НЕ пиши конкретные калории — их посчитает база, используй {{CALORIES}} и {{PROTEIN}}
- Если порции неизвестны — используй {{RANGE_NOTE}}
- Если есть вопросы к пользователю — задай их в response_text

## Стиль ответов
- Коротко, по-дружески, без морализаторства
- "Записал как обед: гречка, куриная грудка. {{CALORIES}} {{RANGE_NOTE}}"
- "Добавил к обеду: яблоко. Теперь {{CALORIES}}"
- "Похоже на приём пищи, но не хватает порций. Сколько примерно грамм гречки было?"
- "Не похоже на еду. Я ничего не записал."

## Примеры

Сообщение: "съел гречку с курицей"
Контекст: пусто
Ответ:
{
  "action": "log_meal",
  "meal_type": "lunch",
  "items": [
    {"name_ru": "гречка", "name_en": "buckwheat groats cooked", "grams": 200, "grams_confidence": "medium", "portion_text": "стандартная порция"},
    {"name_ru": "куриная грудка", "name_en": "chicken breast cooked", "grams": 150, "grams_confidence": "medium", "portion_text": "стандартная порция"}
  ],
  "confidence": "medium",
  "response_text": "Записал как обед: гречка, куриная грудка. {{CALORIES}} {{RANGE_NOTE}}"
}

Сообщение: "и ещё яблоко"
Контекст: последний приём — обед (гречка, куриная грудка)
Ответ:
{
  "action": "append_meal",
  "items": [
    {"name_ru": "яблоко", "name_en": "apple raw", "grams": 150, "grams_confidence": "medium", "portion_text": "одно яблоко"}
  ],
  "confidence": "medium",
  "response_text": "Добавил яблоко к обеду. {{CALORIES}}"
}

Сообщение: "нет, там было 200 грамм риса, а не 150"
Контекст: последний приём — обед (курица 150г, рис 150г)
Ответ:
{
  "action": "update_meal",
  "items": [
    {"name_ru": "курица", "name_en": "chicken breast cooked", "grams": 150, "grams_confidence": "medium", "portion_text": "стандартная порция"},
    {"name_ru": "рис", "name_en": "white rice boiled", "grams": 200, "grams_confidence": "high", "portion_text": "уточнил пользователь"}
  ],
  "confidence": "high",
  "response_text": "Понял, исправил: рис 200 г. {{CALORIES}}"
}

Сообщение: "привет"
Контекст: любой
Ответ:
{
  "action": "unknown",
  "items": [],
  "confidence": "high",
  "response_text": "Привет! Расскажи, что ты съел, и я посчитаю калории."
}

Сообщение: "что я сегодня ел"
Контекст: любой
Ответ:
{
  "action": "show_today",
  "items": [],
  "confidence": "high",
  "response_text": ""
}

Сообщение: "отмени последнее"
Контекст: любой
Ответ:
{
  "action": "delete_last",
  "items": [],
  "confidence": "high",
  "response_text": ""
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

        Args:
            text: User's message
            context: {history, meals_today, totals_today, last_meal, draft_count}

        Returns:
            dict with action, items, meal_type, confidence, response_text
        """
        parts = [f"Сообщение пользователя: {text}", ""]

        if context:
            # Today's meals
            if context.get("meals_today"):
                parts.append("Приёмы пищи за сегодня:")
                for i, m in enumerate(context["meals_today"], 1):
                    items = ", ".join(f"{it['name']} ({it['grams']})" for it in m.get("items", []))
                    status = " (draft)" if m.get("status") == "draft" else ""
                    parts.append(f"  {i}. {m['meal_type']}{status}: {items} — {m.get('calories', '?')}")
                if context.get("totals_today"):
                    t = context["totals_today"]
                    parts.append(f"  Итого: {t.get('calories', '?')}, белок {t.get('protein', '?')}")
                parts.append("")

            # Last meal
            if context.get("last_meal"):
                lm = context["last_meal"]
                items = ", ".join(
                    f"{it.get('name_ru', it.get('name', '?'))} ({it.get('grams', '?')})"
                    for it in lm.get("items", [])
                )
                parts.append(f"Последний приём пищи: {lm.get('meal_type', '?')} — {items} ({lm.get('calories', '?')})")
                if lm.get("original_text"):
                    parts.append(f"  (было написано: \"{lm['original_text']}\")")
                parts.append("")

            # History
            if context.get("history"):
                parts.append("История переписки (последние сообщения):")
                for msg in context["history"]:
                    parts.append(f"  пользователь: {msg['text']}")
                parts.append("")

            # Drafts
            if context.get("draft_count", 0) > 0:
                parts.append(f"У пользователя {context['draft_count']} незавершённых записей (draft).")
                parts.append("")
        else:
            parts.append("Контекст: это первое сообщение, истории пока нет.")
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
