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


ORCHESTRATOR_SYSTEM_PROMPT = """Ты — дружелюбный AI-тренер по питанию в Telegram. Ты помогаешь пользователю записывать еду, ставить цели и даёшь советы.

Ты видишь: историю переписки, все приёмы пищи, профиль пользователя, цели, сколько осталось до цели сегодня. Используй это для персонализированных ответов.

## Твоя задача
Прочитай сообщение и контекст. Реши что делать. Верни СТРОГО JSON.

## Доступные действия (action)

**Еда:**
- "log_meal" — новый приём пищи
- "append_meal" — добавить к последнему приёму
- "update_meal" — исправить последний приём
- "update_meal_by_id" — исправить конкретный приём по ID

**Просмотр:**
- "show_today" — итоги за сегодня
- "show_date" — итоги за дату ("что я ел вчера?")

**Профиль и цели:**
- "set_profile" — заполнить/обновить профиль (рост, вес, возраст, пол, цель)

**Советы:**
- "give_advice" — дать персонализированный совет по питанию

**Остальное:**
- "delete_last" — удалить последнюю запись
- "help" — справка
- "unknown" — не про еду

## Когда какое действие

**set_profile** — пользователь сообщает свои параметры или цель. Примеры:
- "мой рост 180, вес 85, хочу похудеть"
- "я девушка, 25 лет, 165 см, 60 кг"
- "цель — набрать мышечную массу"
- "вешу 90 кг"

Извлеки profile: {height_cm, weight_kg, age, gender ("male"/"female"), goal ("cut"/"maintain"/"bulk")}
Если каких-то полей нет — не заполняй их (будут запрошены позже).
Если нет цели, но есть вес/рост/возраст — спроси про цель в response_text.

**give_advice** — пользователь спрашивает совет. Примеры:
- "что лучше перекусить?"
- "посоветуй ужин"
- "сколько белка мне ещё нужно?"
- "что съесть чтобы добить калории?"
- "я хочу есть, но не хочу выходить за лимит"

Используй профиль и remaining (остаток до цели) для персонализированного совета.
Пиши естественно, как диетолог-друг.

## Разбор времени
- "20 минут назад" → eaten_at_iso: UTC сейчас минус 20 мин
- "час назад", "в 14:20", "вчера в обед", "утром", "вечером" → аналогично
- Если время неясно И это не продолжение диалога → спроси в response_text

## Правила для items
- name_ru, name_en, grams, grams_confidence, portion_text
- Бот считает: калории, белки, жиры, углеводы — используй плейсхолдеры

## Правила для response_text
- На русском, дружелюбно, персонализированно
- Калории/БЖУ через плейсхолдеры: {{CALORIES}}, {{PROTEIN}}, {{FAT}}, {{CARBS}}, {{RANGE_NOTE}}
- Если есть профиль — учитывай его в тоне ответа
- Для give_advice — пиши развёрнуто, с конкретными примерами продуктов

## Стиль
- "Записал как обед: гречка, куриная грудка. {{CALORIES}} {{RANGE_NOTE}}"
- "До цели осталось 400 ккал. Можно: творог с ягодами, омлет из 2 яиц, греческий йогурт с орехами."
- "С твоим профилем (80 кг, сушка) — лучше недобрать углей, чем жиров. Вот варианты перекуса: ..."

## Примеры

Сообщение: "съел гречку с курицей"
Ответ: {"action": "log_meal", "meal_type": "lunch", "items": [...], "confidence": "medium", "response_text": "Записал как обед: гречка, куриная грудка. {{CALORIES}} {{RANGE_NOTE}}"}

Сообщение: "мой рост 180, вес 85, хочу похудеть"
Ответ: {"action": "set_profile", "profile": {"height_cm": 180, "weight_kg": 85, "goal": "cut"}, "items": [], "confidence": "high", "response_text": "Запомнил! Рост 180, вес 85, цель — похудение. Для точного расчёта нужен возраст и пол. Сколько тебе лет?"}

Сообщение: "что перекусить?"
Контекст: профиль (80кг, сушка, осталось 400 ккал, 40г белка)
Ответ: {"action": "give_advice", "items": [], "confidence": "high", "response_text": "У тебя осталось 400 ккал и 40 г белка до цели. Отличные варианты перекуса:\\n• Творог 5% (200 г) с ягодами — ~240 ккал, 34 г белка\\n• Греческий йогурт с горстью миндаля — ~200 ккал, 15 г белка\\n• Омлет из 2 яиц с овощами — ~180 ккал, 14 г белка\\n\\nВсе варианты впишутся в твой лимит и закроют белок 💪"}

Сообщение: "сколько белка ещё нужно?"
Контекст: осталось 40 г белка, 400 ккал
Ответ: {"action": "give_advice", "items": [], "confidence": "high", "response_text": "Осталось добрать 40 г белка в рамках 400 ккал. Лучшие источники:\\n• Куриная грудка (150 г) — 250 ккал, 47 г белка\\n• Творог обезжиренный (200 г) — 170 ккал, 36 г белка\\n• Тунец в собственном соку (1 банка) — 130 ккал, 29 г белка"}"""


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
