"""Telegram bot handlers — LLM decides everything, bot just executes."""

import asyncio
import datetime as dt
import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.types import Message
from aiogram.filters import Command, CommandStart

from app.config import settings
from app.db.database import async_session_factory
from app.db.models import Meal, RawMessage, User
from app.db.repositories import (
    get_today_meals, get_today_totals, get_meals_for_date, get_meal_by_id,
)
from app.schemas.food import (
    ParsedFoodItem, Confidence as C, FoodAnalysis as FA, FoodItem as FI, MealType as MT,
)
from app.services.calculator import calculate_from_parsed
from app.services.food_db import search_food
from app.services.food_analyzer import FoodAnalyzer
from app.services.meal_logger import MealLogger, conf_str
from app.utils.files import download_telegram_photo, download_telegram_voice
from app.utils.time import format_meal_type

logger = logging.getLogger(__name__)
router = Router()

START_TEXT = """Привет! 👋 Я твой трекер питания с AI.

Я считаю калории, белки, жиры и углеводы. Использую базу USDA из 4600+ продуктов.

Что я умею:
• Записывать еду — просто опиши что съел
• Анализировать фото еды (с GigaChat Vision)
• Смотреть итоги за любой день
• Ставить цель (похудеть/поддержание/набор)
• Давать советы по питанию — «что перекусить?», «сколько белка ещё добрать?»

Расскажи о себе: рост, вес, возраст, цель.
Например: «рост 180, вес 85, хочу похудеть до 78»
Или просто начни записывать еду!

Погнали! 💪"""

HELP_TEXT = """Возможности:

🍽 Запись еды
• «съел гречку с курицей»
• «20 минут назад перекусил яблоком»
• «вчера в обед была паста с соусом»

📊 Просмотр
• /today — итоги сегодня
• «что я ел вчера?»

✏️ Исправление
• «нет, там было 200 грамм риса»
• /undo — удалить последнее

🎯 Цели и профиль
• «мой рост 180, вес 85, хочу похудеть»
• «я девушка, 25 лет, 165 см, 60 кг, хочу набрать мышцы»

💬 Советы
• «что лучше перекусить?»
• «сколько белка мне ещё нужно сегодня?»"""


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

async def _build_context(session, user_id: int) -> dict:
    """Build full context for LLM: profile, meals, totals, history."""
    from sqlalchemy import select
    ctx: dict = {}

    user = await session.get(User, user_id)
    if user:
        profile = {}
        for f in ['height_cm', 'weight_kg', 'age', 'gender', 'goal',
                   'target_kcal', 'target_protein_g', 'target_fat_g', 'target_carbs_g']:
            val = getattr(user, f, None)
            if val is not None:
                profile[f] = val
        if profile:
            ctx["profile"] = profile

    now = dt.datetime.utcnow()
    week_ago = now - dt.timedelta(days=7)
    result = await session.execute(
        select(Meal).where(Meal.user_id == user_id, Meal.eaten_at >= week_ago).order_by(Meal.eaten_at.asc()))
    all_meals = result.scalars().all()

    if all_meals:
        ctx["all_meals"] = []
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_idx = 1
        for m in all_meals:
            items = [{"name": it.name, "grams": f"{it.calories_min or '?'}"} for it in m.items]
            entry = {
                "id": m.id, "date": m.eaten_at.strftime("%Y-%m-%d"),
                "time": m.eaten_at.strftime("%H:%M"),
                "meal_type": format_meal_type(m.meal_type), "items": items,
                "calories": f"{m.calories_min}–{m.calories_max} ккал" if m.calories_min else "?",
                "confidence": m.confidence,
            }
            if m.eaten_at >= today_start:
                entry["today_idx"] = today_idx
                today_idx += 1
            ctx["all_meals"].append(entry)

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_meals = [m for m in all_meals if m.eaten_at >= today_start]
    if today_meals:
        t_cal = sum(m.calories_min or 0 for m in today_meals)
        t_prot = sum(m.protein_min_g or 0 for m in today_meals)
        t_fat = sum(m.fat_min_g or 0 for m in today_meals)
        t_carbs = sum(m.carbs_min_g or 0 for m in today_meals)
        ctx["totals_today"] = {"calories": f"{t_cal} ккал", "protein": f"{t_prot:.0f} г",
                               "fat": f"{t_fat:.0f} г", "carbs": f"{t_carbs:.0f} г"}
        if user and user.target_kcal:
            ctx["remaining"] = {
                "kcal": max(0, user.target_kcal - t_cal),
                "protein_g": max(0, (user.target_protein_g or 0) - t_prot),
                "fat_g": max(0, (user.target_fat_g or 0) - t_fat),
                "carbs_g": max(0, (user.target_carbs_g or 0) - t_carbs),
            }

    result = await session.execute(
        select(RawMessage).where(RawMessage.user_id == user_id).order_by(RawMessage.created_at.desc()).limit(15))
    recent = list(result.scalars()); recent.reverse()
    ctx["history"] = [{"role": "user", "text": rm.text or "(фото)"} for rm in recent]
    return ctx


def _parse_eaten_at(eaten_at_iso: str | None) -> dt.datetime | None:
    if not eaten_at_iso: return None
    try:
        parsed = dt.datetime.fromisoformat(eaten_at_iso)
        if parsed.tzinfo is None: parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.replace(tzinfo=None)
    except (ValueError, TypeError): return None


def _inject_numbers(text: str, analysis, confidence: str, items_str: str = "") -> str:
    cal = f"{analysis.total_calories_min}–{analysis.total_calories_max} ккал"
    prot = f"{analysis.total_protein_min_g:.0f}–{analysis.total_protein_max_g:.0f} г белка"
    fat = f"{analysis.total_fat_min_g:.0f}–{analysis.total_fat_max_g:.0f} г жиров"
    carbs = f"{analysis.total_carbs_min_g:.0f}–{analysis.total_carbs_max_g:.0f} г углеводов"
    note = "" if confidence == "high" else "Это диапазон — точный вес неизвестен. Уточни граммы, и я посчитаю точнее."
    return (text.replace("{{CALORIES}}", cal).replace("{{PROTEIN}}", prot)
            .replace("{{FAT}}", fat).replace("{{CARBS}}", carbs)
            .replace("{{RANGE_NOTE}}", note).replace("{{ITEMS}}", items_str))


def _format_meals(meals, totals: dict | None = None, date_label: str = "Сегодня") -> str:
    if not meals: return f"{date_label} нет записей. Напиши, что съел!"
    lines = [f"{date_label}:\n"]
    for i, meal in enumerate(meals, 1):
        mt = format_meal_type(meal.meal_type)
        items_str = ", ".join(it.name for it in meal.items) if meal.items else "—"
        cal = f"{meal.calories_min}–{meal.calories_max} ккал" if meal.calories_min else ""
        macros = ", ".join(x for x in [
            f"Б:{meal.protein_min_g:.0f}г" if meal.protein_min_g else "",
            f"Ж:{meal.fat_min_g:.0f}г" if meal.fat_min_g else "",
            f"У:{meal.carbs_min_g:.0f}г" if meal.carbs_min_g else "",
        ] if x)
        time_str = meal.eaten_at.strftime("%H:%M") if meal.eaten_at else ""
        details = f" — {cal}, {macros}" if cal else ""
        lines.append(f"{i}. [{time_str}] {mt.capitalize()} — {items_str}{details}")
    if totals:
        lines.append(f"\nИтого: {totals.get('calories', '?')} | "
                     f"Б:{totals.get('protein_min_g', 0):.0f}г "
                     f"Ж:{totals.get('fat_min_g', 0):.0f}г "
                     f"У:{totals.get('carbs_min_g', 0):.0f}г")
    return "\n".join(lines)


async def send_animated(message: Message, text: str, chunk_delay: float = 0.06) -> None:
    words = text.split()
    if len(words) <= 8: await message.answer(text); return
    chunks = []
    for i in range(0, len(words), 3):
        chunk = " ".join(words[i : i + 3])
        chunks.append(chunks[-1] + " " + chunk if chunks else chunk)
    sent = await message.answer(chunks[0] + " ▌")
    for c in chunks[1:]:
        await asyncio.sleep(chunk_delay)
        try: await sent.edit_text(c + " ▌")
        except Exception: pass
    await asyncio.sleep(chunk_delay * 2)
    try: await sent.edit_text(text)
    except Exception: pass


# ═══════════════════════════════════════════════════════════════════════════════
# Nutrition pipeline — USDA lookup + calculator
# ═══════════════════════════════════════════════════════════════════════════════

async def _run_nutrition_pipeline(session, result: dict):
    parsed = [ParsedFoodItem(
        name_ru=it.get("name_ru", it.get("name", "")),
        name_en=it.get("name_en", it.get("name", "")),
        grams=float(it.get("grams", 100)),
        grams_confidence=it.get("grams_confidence", "medium"),
        portion_text=it.get("portion_text", ""),
        manual_kcal=it.get("manual_kcal"),
        manual_protein_g=it.get("manual_protein_g"),
        manual_fat_g=it.get("manual_fat_g"),
        manual_carbs_g=it.get("manual_carbs_g"),
    ) for it in result.get("items", [])]

    # USDA lookup — skip for manual items
    food_matches = []
    for pi in parsed:
        if pi.is_manual:
            food_matches.append(None)  # manual items skip USDA
        else:
            matches = await search_food(session, pi.name_en or pi.name_ru, limit=1)
            food_matches.append(matches[0] if matches else None)

    calc = calculate_from_parsed([p.model_dump() for p in parsed], food_matches)

    conf = C(result.get("confidence", "medium"))
    mt = MT(result.get("meal_type", "unknown"))

    items = [FI(
        name=pi.name_ru or pi.name_en, portion_text=pi.portion_text or f"~{ci.grams:.0f} г",
        calories_min=int(ci.kcal * 0.85), calories_max=int(ci.kcal * 1.15),
        protein_min_g=round(ci.protein_g * 0.85, 1), protein_max_g=round(ci.protein_g * 1.15, 1),
        fat_min_g=round(ci.fat_g * 0.85, 1), fat_max_g=round(ci.fat_g * 1.15, 1),
        carbs_min_g=round(ci.carbs_g * 0.85, 1), carbs_max_g=round(ci.carbs_g * 1.15, 1),
        confidence=C(ci.confidence),
    ) for pi, ci in zip(parsed, calc.items)]

    analysis = FA(
        is_food=True, meal_type=mt, items=items,
        total_calories_min=round(calc.total_kcal * 0.85),
        total_calories_max=round(calc.total_kcal * 1.15),
        total_protein_min_g=round(calc.total_protein_g * 0.85, 1),
        total_protein_max_g=round(calc.total_protein_g * 1.15, 1),
        total_fat_min_g=round(calc.total_fat_g * 0.85, 1),
        total_fat_max_g=round(calc.total_fat_g * 1.15, 1),
        total_carbs_min_g=round(calc.total_carbs_g * 0.85, 1),
        total_carbs_max_g=round(calc.total_carbs_g * 1.15, 1),
        confidence=conf, parsed_items=parsed,
    )
    return items, parsed, analysis


# ═══════════════════════════════════════════════════════════════════════════════
# Shared action dispatcher — used by both text and voice handlers
# ═══════════════════════════════════════════════════════════════════════════════

def _get_llm():
    s = settings
    if s.ai_provider == "gigachat" and s.gigachat_credentials:
        from app.providers.gigachat import GigaChatProvider
        return GigaChatProvider()
    elif s.ai_provider == "yandex" and s.yandex_api_key:
        from app.providers.yandex import YandexGPTProvider
        return YandexGPTProvider()
    else:
        from app.providers.mock import MockProvider
        return MockProvider()


async def _dispatch_action(
    action: str, result: dict, ctx: dict, session,
    ml: MealLogger, uid: int, telegram_id: int, text: str, message: Message,
) -> str | None:
    """Execute an LLM-decided action. Returns None if action handled, error string if unknown."""
    llm_response = result.get("response_text", "")
    llm_confidence = result.get("confidence", "medium")

    if action == "set_profile":
        profile_data = result.get("profile", {})
        if profile_data:
            await ml.set_profile(uid, **profile_data)
            user = await ml.get_user(uid)
            resp = llm_response or "Профиль обновлён!"
            if user and user.target_kcal:
                resp += f"\n\nТвоя цель: {user.target_kcal} ккал, Б:{user.target_protein_g}г Ж:{user.target_fat_g}г У:{user.target_carbs_g}г"
            await send_animated(message, resp)
        else:
            await message.answer("Не понял твои данные. Напиши, например: «рост 180, вес 85, хочу похудеть».")
        return None

    if action == "give_advice":
        await send_animated(message, llm_response or "Подумал над твоим вопросом, но не смог сформулировать ответ.")
        return None

    if action == "show_today":
        meals = await get_today_meals(session, uid)
        totals = await get_today_totals(session, uid)
        resp = _format_meals(meals, totals, "Сегодня")
        if ctx.get("remaining"):
            r = ctx["remaining"]
            resp += f"\n\nОсталось до цели: {r['kcal']} ккал, Б:{r['protein_g']:.0f}г Ж:{r['fat_g']:.0f}г У:{r['carbs_g']:.0f}г"
        await send_animated(message, resp)
        return None

    if action == "show_date":
        date_str = result.get("date", "")
        if date_str:
            meals = await get_meals_for_date(session, uid, date_str)
            await send_animated(message, _format_meals(meals, None, f"📅 {date_str}"))
        else:
            meals = await get_today_meals(session, uid)
            totals = await get_today_totals(session, uid)
            await send_animated(message, _format_meals(meals, totals, "Сегодня"))
        return None

    if action == "delete_last":
        desc = await ml.delete_last_meal(uid)
        await ml.clear_last_meal_id(uid)
        await message.answer(llm_response or (f"Убрал: {desc}." if desc else "Нечего удалять."))
        return None

    if action == "help":
        await send_animated(message, HELP_TEXT)
        return None

    if action == "unknown":
        await message.answer(llm_response or "Я не совсем понял. Расскажи, что ты съел!")
        return None

    # ── Food actions ──
    if action in ("log_meal", "append_meal", "update_meal", "update_meal_by_id"):
        eaten_at = _parse_eaten_at(result.get("eaten_at_iso"))
        items_list = result.get("items", [])

        # Metadata-only update: no items, just changing type/time
        if action in ("update_meal", "update_meal_by_id") and not items_list:
            raw_id = result.get("meal_id") if action == "update_meal_by_id" else await ml.get_last_meal_id(uid)
            if not raw_id:
                await message.answer("Не понял какую запись исправить.")
                return None

            meal = await get_meal_by_id(session, raw_id)
            if (not meal or meal.user_id != uid) and ctx.get("all_meals"):
                for m in ctx["all_meals"]:
                    if m.get("today_idx") == raw_id or m["id"] == raw_id:
                        meal = await get_meal_by_id(session, m["id"])
                        break
            if not meal or meal.user_id != uid:
                await message.answer(f"Не могу найти запись #{raw_id}.")
                return None

            new_type = result.get("meal_type")
            new_time = _parse_eaten_at(result.get("eaten_at_iso"))
            if new_type:
                meal.meal_type = new_type
            if new_time:
                meal.eaten_at = new_time
            meal.updated_at = dt.datetime.utcnow()
            await session.flush()

            time_str = meal.eaten_at.strftime('%H:%M') if meal.eaten_at else ''
            resp = llm_response or f"Исправил: {format_meal_type(meal.meal_type)}, {time_str}"
            await send_animated(message, resp)
            return None

        # Normal food action with items
        items, parsed, analysis = await _run_nutrition_pipeline(session, result)
        if not parsed:
            await message.answer("Не смог разобрать что за еда. Опиши подробнее!")
            return None

        if action == "log_meal":
            meal, _ = await ml.log_from_text(uid, text, analysis, eaten_at)
            if meal:
                await ml.set_last_meal_id(uid, meal.id)

        elif action == "append_meal":
            last_id = await ml.get_last_meal_id(uid)
            if last_id is None:
                await message.answer("Не к чему добавлять. Расскажи, что съел, и я запишу как новый приём.")
                return None
            meal, _ = await ml.append_to_meal(last_id, text, analysis)
            if meal and meal.items:
                for attr in ['calories', 'protein', 'fat', 'carbs']:
                    setattr(analysis, f'total_{attr}_min', sum(getattr(it, f'{attr}_min') or 0 for it in meal.items))
                    setattr(analysis, f'total_{attr}_max', sum(getattr(it, f'{attr}_max') or 0 for it in meal.items))

        elif action == "update_meal":
            last_id = await ml.get_last_meal_id(uid)
            if last_id is None:
                await message.answer("Нечего исправлять."); return None
            meal, _ = await ml.update_meal(last_id, text, analysis)
            if meal and eaten_at: meal.eaten_at = eaten_at

        elif action == "update_meal_by_id":
            raw_id = result.get("meal_id")
            if not raw_id:
                await message.answer("Не понял какую запись исправить."); return None
            meal_id = raw_id
            meal = await get_meal_by_id(session, meal_id)
            if (not meal or meal.user_id != uid) and ctx.get("all_meals"):
                for m in ctx["all_meals"]:
                    if m.get("today_idx") == raw_id or m["id"] == raw_id:
                        meal_id = m["id"]
                        meal = await get_meal_by_id(session, meal_id)
                        break
            if not meal or meal.user_id != uid:
                await message.answer(f"Не могу найти запись #{raw_id}."); return None
            meal, _ = await ml.update_meal(meal_id, text, analysis)
            if meal and eaten_at: meal.eaten_at = eaten_at

        items_str = ", ".join(it.name for it in items)
        response = _inject_numbers(
            llm_response or f"Записал как {format_meal_type(analysis.meal_type.value)}: {{ITEMS}}. {{CALORIES}} {{RANGE_NOTE}}",
            analysis, llm_confidence, items_str,
        )
        await send_animated(message, response)
        return None

    return "unknown_action"


# ═══════════════════════════════════════════════════════════════════════════════
# Command handlers
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot) -> None:
    await send_animated(message, START_TEXT)

@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await send_animated(message, HELP_TEXT)

@router.message(Command("today"))
async def cmd_today(message: Message, bot: Bot) -> None:
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    async with async_session_factory() as session:
        ml = MealLogger(session)
        uid = await ml.ensure_user(telegram_id=message.from_user.id, username=message.from_user.username,
                                    first_name=message.from_user.first_name or "")
        meals = await get_today_meals(session, uid)
        totals = await get_today_totals(session, uid)
        await send_animated(message, _format_meals(meals, totals, "Сегодня"))

@router.message(Command("undo"))
async def cmd_undo(message: Message, bot: Bot) -> None:
    async with async_session_factory() as session:
        ml = MealLogger(session)
        uid = await ml.ensure_user(telegram_id=message.from_user.id, username=message.from_user.username,
                                    first_name=message.from_user.first_name or "")
        desc = await ml.delete_last_meal(uid)
        await ml.clear_last_meal_id(uid)
        await message.answer(f"Убрал: {desc}." if desc else "Нечего удалять.")
        await session.commit()

@router.message(Command("delete"))
async def cmd_delete(message: Message, bot: Bot) -> None:
    args = message.text.strip().split()
    if len(args) < 2 or not args[1].isdigit(): await message.answer("Напиши номер: /delete 3"); return
    n = int(args[1])
    async with async_session_factory() as session:
        ml = MealLogger(session)
        uid = await ml.ensure_user(telegram_id=message.from_user.id, username=message.from_user.username,
                                    first_name=message.from_user.first_name or "")
        desc = await ml.delete_meal_by_number(uid, n)
        await message.answer(f"Убрал #{n}: {desc}." if desc else f"Запись #{n} не найдена.")
        await session.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# Main text handler
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(F.text)
async def handle_text(message: Message, bot: Bot) -> None:
    text = message.text.strip() if message.text else ""
    if not text: await message.answer("Расскажи, что ты съел!"); return

    telegram_id = message.from_user.id
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    async with async_session_factory() as session:
        ml = MealLogger(session)
        uid = await ml.ensure_user(telegram_id=telegram_id, username=message.from_user.username,
                                    first_name=message.from_user.first_name or "")
        await ml.log_raw_message(user_id=uid, telegram_message_id=message.message_id, message_type="text", text=text)

        ctx = await _build_context(session, uid)
        if message.reply_to_message and message.reply_to_message.text:
            ctx["reply_to"] = message.reply_to_message.text[:500]

        llm = _get_llm()
        result = await llm.orchestrate(text, ctx)
        action = result.get("action", "unknown")
        logger.info(f"Orchestrator: action={action} confidence={result.get('confidence')}")

        error = await _dispatch_action(action, result, ctx, session, ml, uid, telegram_id, text, message)
        if error:
            await message.answer("Я не совсем понял. Расскажи, что ты съел!")

        await session.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# Photo handler
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(F.photo)
async def handle_photo(message: Message, bot: Bot) -> None:
    caption = message.caption.strip() if message.caption else None
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    async with async_session_factory() as session:
        ml = MealLogger(session)
        uid = await ml.ensure_user(telegram_id=message.from_user.id, username=message.from_user.username,
                                    first_name=message.from_user.first_name or "")
        photo_path = await download_telegram_photo(bot, message.photo[-1].file_id)
        if photo_path is None: await message.answer("Не удалось скачать фото."); return
        await ml.log_raw_message(user_id=uid, telegram_message_id=message.message_id,
                                  message_type="photo_with_caption" if caption else "photo",
                                  text=caption, photo_path=str(photo_path))

        # Label detection
        is_label = False
        if caption:
            label_kw = ["этикетк", "упаковк", "состав", "label", "nutrition", "пищев", "кбжу", "калорийн"]
            if any(kw in caption.lower() for kw in label_kw):
                is_label = True

        s = settings
        if is_label and s.ai_provider == "gigachat" and s.gigachat_credentials:
            from app.providers.gigachat import GigaChatProvider
            gc = GigaChatProvider()
            label_data = await gc.analyze_label_photo(str(photo_path))
            if label_data.get("is_label"):
                kcal = label_data.get("kcal_per_100g", 0)
                prot = label_data.get("protein_per_100g", 0)
                fat = label_data.get("fat_per_100g", 0)
                carbs = label_data.get("carbs_per_100g", 0)
                serving = label_data.get("serving_size_g", 100)
                name_ru = label_data.get("name_ru", "продукт с этикетки")

                parsed = [ParsedFoodItem(name_ru=name_ru, name_en=name_ru, grams=serving,
                            grams_confidence="high", portion_text=f"с этикетки, порция {serving} г",
                            manual_kcal=kcal, manual_protein_g=prot, manual_fat_g=fat, manual_carbs_g=carbs)]
                analysis = FA(is_food=True, meal_type=MT("snack"), items=[
                    FI(name=name_ru, portion_text=f"{serving} г", calories_min=int(kcal*0.95),
                       calories_max=int(kcal*1.05), protein_min_g=prot*0.95, protein_max_g=prot*1.05,
                       fat_min_g=fat*0.95, fat_max_g=fat*1.05, carbs_min_g=carbs*0.95, carbs_max_g=carbs*1.05,
                       confidence=C("high"))
                ], total_calories_min=int(kcal*0.95), total_calories_max=int(kcal*1.05),
                   total_protein_min_g=prot*0.95, total_protein_max_g=prot*1.05,
                   total_fat_min_g=fat*0.95, total_fat_max_g=fat*1.05,
                   total_carbs_min_g=carbs*0.95, total_carbs_max_g=carbs*1.05,
                   confidence=C("high"), parsed_items=parsed)
                meal, _ = await ml.log_from_photo(uid, str(photo_path), caption, analysis)
                if meal: await ml.set_last_meal_id(uid, meal.id)
                await send_animated(message, f"Прочитал этикетку: {name_ru}, {serving} г — {kcal} ккал, Б:{prot:.0f}г Ж:{fat:.0f}г У:{carbs:.0f}г")
                await session.commit(); return

        analyzer = FoodAnalyzer()
        if not analyzer.has_vision:
            await message.answer("Фото сохранил, но vision-модель пока не настроена. Опиши, что там было, и я запишу.")
            await session.commit(); return

        analysis = await analyzer.analyze_photo(str(photo_path), caption)
        meal, _ = await ml.log_from_photo(uid, str(photo_path), caption, analysis)
        if meal: await ml.set_last_meal_id(uid, meal.id)
        await send_animated(message, "Проанализировал фото и записал!")
        await session.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# Voice handler
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(F.voice)
async def handle_voice(message: Message, bot: Bot) -> None:
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    # Download and transcribe voice
    voice_bytes = await download_telegram_voice(bot, message.voice.file_id)
    if voice_bytes is None:
        await message.answer("Не удалось скачать голосовое сообщение."); return

    from app.providers.speech import get_stt_provider
    stt = get_stt_provider()
    if stt is None:
        await message.answer("Распознавание речи не настроено. Напиши текстом!"); return

    text = await stt.transcribe(voice_bytes)
    if not text:
        await message.answer("Не удалось распознать речь. Напиши текстом!"); return

    await message.answer(f"🎤 *{text}*", parse_mode="Markdown")

    # Process transcribed text through the same pipeline as text handler
    async with async_session_factory() as session:
        ml = MealLogger(session)
        uid = await ml.ensure_user(telegram_id=message.from_user.id, username=message.from_user.username,
                                    first_name=message.from_user.first_name or "")
        await ml.log_raw_message(user_id=uid, telegram_message_id=message.message_id,
                                  message_type="text", text=f"[voice] {text}")

        ctx = await _build_context(session, uid)
        if message.reply_to_message and message.reply_to_message.text:
            ctx["reply_to"] = message.reply_to_message.text[:500]

        llm = _get_llm()
        result = await llm.orchestrate(text, ctx)
        action = result.get("action", "unknown")
        logger.info(f"Voice orchestrator: action={action}")

        error = await _dispatch_action(action, result, ctx, session, ml, uid, message.from_user.id, text, message)
        if error:
            await message.answer("Я не совсем понял. Расскажи, что ты съел!")

        await session.commit()
