"""GigaChat provider — text orchestration + vision in one API.

Uses GigaChat API via httpx (no extra SDK needed).
Auth: OAuth2 (client_id:client_secret → Bearer token).
Models: GigaChat-2-Max (text + vision), GigaChat-2-Pro, GigaChat-Pro.
"""

import base64
import datetime as dt
import json
import logging
from pathlib import Path

import httpx

from app.config import settings
from app.providers.base import BaseFoodTextProvider, BaseIntentProvider, BaseVisionProvider
from app.schemas.food import Confidence, FoodAnalysis, FoodItem, MealType, ParsedFoodItem
from app.schemas.intent import IntentResult, IntentType

logger = logging.getLogger(__name__)

GIGACHAT_AUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
GIGACHAT_API_URL = "https://gigachat.devices.sberbank.ru/api/v1"

# ── Reuse the same orchestrator prompt from YandexGPT ───────────────────────
from app.providers.yandex import ORCHESTRATOR_SYSTEM_PROMPT

VISION_SYSTEM_PROMPT = """Ты — анализатор фотографий еды для трекера питания.
Посмотри на фото и верни СТРОГО JSON.

Правила:
1. Определи is_food (true/false). Если на фото нет еды — is_food: false.
2. Определи meal_type_guess: breakfast/lunch/dinner/snack/unknown.
3. Выдели отдельные блюда/продукты в items:
   - name_ru: название на русском
   - name_en: название на английском для поиска в базе USDA
   - grams: примерный вес в граммах (визуальная оценка)
   - grams_confidence: "low" (визуальная оценка всегда неточная)
   - portion_text: почему такой вес
4. Определи общую confidence (для фото почти всегда "low" или "medium")
5. Если что-то неясно — добавь вопросы в questions
6. НЕ считай калории — это сделает база

Пример:
{"is_food": true, "meal_type_guess": "lunch",
 "items": [{"name_ru": "куриная грудка", "name_en": "chicken breast cooked", "grams": 150, "grams_confidence": "low", "portion_text": "визуально ~150 г"}],
 "confidence": "low", "questions": []}"""

LABEL_VISION_PROMPT = """Ты читаешь этикетку/упаковку продукта с фото.
Найди и извлеки ВСЕ числовые значения. Верни СТРОГО JSON.

Правила:
1. Найди название продукта → name_ru, name_en
2. Найди КАЛОРИЙНОСТЬ (ккал на 100г или на порцию) → укажи per_100g: true/false
3. Найди БЕЛКИ, ЖИРЫ, УГЛЕВОДЫ (граммы на 100г или на порцию)
4. Если указан вес порции — извлеки serving_size_g
5. Если нашёл состав/ингредиенты — кратко опиши
6. ВСЕ цифры которые удалось прочитать — помести в поля

Формат ответа:
{
  "is_label": true,
  "name_ru": "название с упаковки",
  "name_en": "product name in English",
  "kcal_per_100g": 250,
  "protein_per_100g": 10.5,
  "fat_per_100g": 12.0,
  "carbs_per_100g": 30.0,
  "serving_size_g": 100,
  "per_100g": true,
  "confidence": "high",
  "questions": []
}

Если на фото НЕ этикетка — верни {"is_label": false}"""


class GigaChatProvider(BaseFoodTextProvider, BaseIntentProvider, BaseVisionProvider):
    """GigaChat provider — handles text orchestration and vision.

    Uses OAuth2 token flow. Supports GigaChat-2-Max (text + vision).
    """

    def __init__(self):
        self._token: str | None = None
        self._token_expires: dt.datetime | None = None

    # ── Token management ─────────────────────────────────────────────────

    async def _ensure_token(self) -> str:
        """Get or refresh OAuth token."""
        if self._token and self._token_expires and dt.datetime.utcnow() < self._token_expires:
            return self._token

        credentials = settings.gigachat_credentials
        if not credentials:
            raise RuntimeError("GIGACHAT_CREDENTIALS not set")

        auth_header = base64.b64encode(credentials.encode()).decode()

        async with httpx.AsyncClient(timeout=30, verify=False) as client:
            resp = await client.post(
                GIGACHAT_AUTH_URL,
                headers={
                    "Authorization": f"Basic {auth_header}",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                    "RqUID": "calai-bot-request",
                },
                data={"scope": "GIGACHAT_API_PERS"},
            )
            resp.raise_for_status()
            data = resp.json()

        self._token = data["access_token"]
        # Tokens live ~30 min, refresh after 25
        self._token_expires = dt.datetime.utcnow() + dt.timedelta(minutes=25)
        return self._token

    # ── Text: orchestrator ───────────────────────────────────────────────

    async def orchestrate(self, text: str, context: dict | None = None) -> dict:
        """LLM decides everything — same interface as YandexGPT."""
        parts = [f"Сообщение пользователя: {text}", ""]

        if context:
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

            if context.get("totals_today"):
                t = context["totals_today"]
                parts.append(f"Итого сегодня: {t['calories']}, белок {t['protein']}")
                parts.append("")

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
            raw = await self._chat_completion(ORCHESTRATOR_SYSTEM_PROMPT, user_message, json_mode=True)
            return self._parse_raw_json(raw)
        except Exception as e:
            logger.error(f"GigaChat orchestrator failed: {e}")
            return {"action": "unknown", "items": [], "confidence": "low", "response_text": "", "_error": str(e)}

    # ── Vision: photo analysis ───────────────────────────────────────────

    async def analyze_food_photo(self, photo_path: str, caption: str | None = None) -> FoodAnalysis:
        """Analyze food photo via GigaChat Vision."""
        photo = Path(photo_path)
        if not photo.exists():
            return FoodAnalysis(is_food=False, confidence=Confidence.LOW,
                                questions=["Не могу найти фото. Отправь ещё раз."])

        try:
            token = await self._ensure_token()

            # Step 1: Upload file
            async with httpx.AsyncClient(timeout=60, verify=False) as client:
                with open(photo_path, "rb") as f:
                    upload_resp = await client.post(
                        f"{GIGACHAT_API_URL}/files",
                        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                        files={"file": (photo.name, f, "image/jpeg")},
                    )
                upload_resp.raise_for_status()
                file_id = upload_resp.json()["id"]

            # Step 2: Send vision prompt
            user_text = caption or "Что на этом фото? Это еда? Проанализируй."
            raw = await self._chat_completion_with_attachment(
                VISION_SYSTEM_PROMPT, user_text, file_id, json_mode=True
            )

            data = self._parse_raw_json(raw)
            return self._parse_vision_response(data, raw)

        except Exception as e:
            logger.error(f"GigaChat vision failed: {e}")
            return FoodAnalysis(is_food=False, confidence=Confidence.LOW,
                                questions=["Не удалось проанализировать фото. Опиши, что там было, текстом."])

    def _parse_vision_response(self, data: dict, raw: str) -> FoodAnalysis:
        """Parse vision response into FoodAnalysis."""
        if not data.get("is_food", True):
            return FoodAnalysis(is_food=False, confidence=Confidence.LOW,
                                questions=["Не похоже на еду."])

        items = []
        parsed = []
        for it in data.get("items", []):
            items.append(FoodItem(
                name=it.get("name_ru", it.get("name", "")),
                portion_text=it.get("portion_text", ""),
                confidence=Confidence(it.get("grams_confidence", "low")),
            ))
            parsed.append(ParsedFoodItem(
                name_ru=it.get("name_ru", it.get("name", "")),
                name_en=it.get("name_en", it.get("name", "")),
                grams=float(it.get("grams", 100)),
                grams_confidence=it.get("grams_confidence", "low"),
                portion_text=it.get("portion_text", ""),
            ))

        return FoodAnalysis(
            is_food=True,
            meal_type=MealType(data.get("meal_type_guess", data.get("meal_type", "unknown"))),
            items=items,
            confidence=Confidence(data.get("confidence", "low")),
            questions=data.get("questions", []),
            raw_response=raw,
            parsed_items=parsed,
        )

    async def analyze_label_photo(self, photo_path: str) -> dict:
        """Analyze a nutrition label / package photo. Returns structured nutrition data."""
        photo = Path(photo_path)
        if not photo.exists():
            return {"is_label": False, "error": "file not found"}

        try:
            token = await self._ensure_token()

            # Upload
            async with httpx.AsyncClient(timeout=60, verify=False) as client:
                with open(photo_path, "rb") as f:
                    upload_resp = await client.post(
                        f"{GIGACHAT_API_URL}/files",
                        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                        files={"file": (photo.name, f, "image/jpeg")},
                    )
                upload_resp.raise_for_status()
                file_id = upload_resp.json()["id"]

            raw = await self._chat_completion_with_attachment(
                LABEL_VISION_PROMPT, "Прочитай этикетку и извлеки все цифры.", file_id, json_mode=True
            )
            return self._parse_raw_json(raw)
        except Exception as e:
            logger.error(f"Label analysis failed: {e}")
            return {"is_label": False, "error": str(e)}

    # ── Legacy interface (compatibility) ─────────────────────────────────

    async def detect_intent(self, text: str) -> IntentResult:
        try:
            result = await self.orchestrate(text)
            return IntentResult(intent=IntentType(result.get("action", "unknown")),
                               confidence=float(result.get("confidence", 0.5)),
                               reasoning=result.get("response_text", ""))
        except Exception as e:
            return IntentResult(intent=IntentType.UNKNOWN, confidence=0.0, reasoning=str(e))

    async def analyze_food_text(self, text: str, context: dict | None = None) -> FoodAnalysis:
        result = await self.orchestrate(text, context)
        items = []
        for it in result.get("items", []):
            items.append(FoodItem(
                name=it.get("name_ru", it.get("name", "")),
                portion_text=it.get("portion_text"),
                confidence=Confidence(it.get("grams_confidence", "medium")),
            ))
        return FoodAnalysis(
            is_food=result.get("action") in ("log_meal", "append_meal", "update_meal", "update_meal_by_id"),
            meal_type=MealType(result.get("meal_type", "unknown")),
            items=items,
            confidence=Confidence(result.get("confidence", "medium")),
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

    # ── API helpers ───────────────────────────────────────────────────────

    async def _chat_completion(self, system_prompt: str, user_message: str, json_mode: bool = True) -> str:
        """Call GigaChat chat/completions."""
        token = await self._ensure_token()
        model = settings.gigachat_model or "GigaChat-2-Max"

        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": 0.3,
            "max_tokens": 4000,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        async with httpx.AsyncClient(timeout=90, verify=False) as client:
            resp = await client.post(
                f"{GIGACHAT_API_URL}/chat/completions",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()

        text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not text:
            raise RuntimeError("Empty response from GigaChat")
        return text

    async def _chat_completion_with_attachment(
        self, system_prompt: str, user_text: str, file_id: str, json_mode: bool = True
    ) -> str:
        """Call GigaChat with an image attachment."""
        token = await self._ensure_token()
        model = settings.gigachat_model or "GigaChat-2-Max"

        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text, "attachments": [file_id]},
            ],
            "temperature": 0.3,
            "max_tokens": 2000,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        async with httpx.AsyncClient(timeout=90, verify=False) as client:
            resp = await client.post(
                f"{GIGACHAT_API_URL}/chat/completions",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()

        return data.get("choices", [{}])[0].get("message", {}).get("content", "")

    def _parse_raw_json(self, raw: str) -> dict:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].startswith("```"): lines = lines[1:]
            if lines and lines[-1].startswith("```"): lines = lines[:-1]
            text = "\n".join(lines)
        return json.loads(text)
