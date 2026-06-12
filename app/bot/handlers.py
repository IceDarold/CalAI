"""Telegram bot handlers — thin layer, delegates to services."""

import asyncio
import datetime as dt
import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.types import Message
from aiogram.filters import Command, CommandStart

from app.config import settings
from app.db.database import async_session_factory
from app.db.repositories import (
    get_today_meals, get_today_totals, get_meals_for_date, get_meal_by_id,
)
from app.providers import get_vision_provider
from app.providers.context_format import format_context_for_llm
from app.schemas.food import (
    ParsedFoodItem, Confidence as C, FoodAnalysis as FA, FoodItem as FI, MealType as MT,
)
from app.services.context import build_context
from app.services.nutrition import run_nutrition_pipeline
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


def _get_llm():
    """Use the provider factory — single source of truth for provider selection."""
    from app.providers import get_text_provider
    return get_text_provider()


# ═══════════════════════════════════════════════════════════════════════════════
# Action dispatcher — shared by text and voice handlers
# ═══════════════════════════════════════════════════════════════════════════════

async def _dispatch_action(
    action: str, result: dict, ctx: dict, session,
    ml: MealLogger, uid: int, telegram_id: int, text: str, message: Message,
) -> str | None:
    """Execute LLM-decided action. Returns None on success, error string on failure."""
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

        # Metadata-only update
        if action in ("update_meal", "update_meal_by_id") and not items_list:
            raw_id = result.get("meal_id") if action == "update_meal_by_id" else await ml.get_last_meal_id(uid)
            if not raw_id:
                await message.answer("Не понял какую запись исправить."); return None
            meal = await get_meal_by_id(session, raw_id)
            if (not meal or meal.user_id != uid) and ctx.get("all_meals"):
                for m in ctx["all_meals"]:
                    if m.get("today_idx") == raw_id or m["id"] == raw_id:
                        meal = await get_meal_by_id(session, m["id"]); break
            if not meal or meal.user_id != uid:
                await message.answer(f"Не могу найти запись #{raw_id}."); return None
            new_type = result.get("meal_type")
            new_time = _parse_eaten_at(result.get("eaten_at_iso"))
            if new_type: meal.meal_type = new_type
            if new_time: meal.eaten_at = new_time
            meal.updated_at = dt.datetime.utcnow()
            await session.flush()
            time_str = meal.eaten_at.strftime('%H:%M') if meal.eaten_at else ''
            await send_animated(message, llm_response or f"Исправил: {format_meal_type(meal.meal_type)}, {time_str}")
            return None

        # Normal food action
        items, parsed, analysis = await run_nutrition_pipeline(session, result)
        if not parsed:
            await message.answer("Не смог разобрать что за еда. Опиши подробнее!"); return None

        if action == "log_meal":
            meal, _ = await ml.log_from_text(uid, text, analysis, eaten_at)
            if meal: await ml.set_last_meal_id(uid, meal.id)

        elif action == "append_meal":
            last_id = await ml.get_last_meal_id(uid)
            if last_id is None:
                await message.answer("Не к чему добавлять. Расскажи, что съел, и я запишу как новый приём."); return None
            meal, _ = await ml.append_to_meal(last_id, text, analysis)
            if meal and meal.items:
                for attr in ['calories', 'protein', 'fat', 'carbs']:
                    setattr(analysis, f'total_{attr}_min', sum(getattr(it, f'{attr}_min') or 0 for it in meal.items))
                    setattr(analysis, f'total_{attr}_max', sum(getattr(it, f'{attr}_max') or 0 for it in meal.items))

        elif action == "update_meal":
            last_id = await ml.get_last_meal_id(uid)
            if last_id is None: await message.answer("Нечего исправлять."); return None
            meal, _ = await ml.update_meal(last_id, text, analysis)
            if meal and eaten_at: meal.eaten_at = eaten_at

        elif action == "update_meal_by_id":
            raw_id = result.get("meal_id")
            if not raw_id: await message.answer("Не понял какую запись исправить."); return None
            meal_id = raw_id
            meal = await get_meal_by_id(session, meal_id)
            if (not meal or meal.user_id != uid) and ctx.get("all_meals"):
                for m in ctx["all_meals"]:
                    if m.get("today_idx") == raw_id or m["id"] == raw_id:
                        meal_id = m["id"]; meal = await get_meal_by_id(session, meal_id); break
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
# Text handler
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(F.text)
async def handle_text(message: Message, bot: Bot) -> None:
    text = message.text.strip() if message.text else ""
    if not text: await message.answer("Расскажи, что ты съел!"); return

    telegram_id = message.from_user.id
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    async with async_session_factory() as session:
        try:
            ml = MealLogger(session)
            uid = await ml.ensure_user(telegram_id=telegram_id, username=message.from_user.username,
                                        first_name=message.from_user.first_name or "")
            await ml.log_raw_message(user_id=uid, telegram_message_id=message.message_id, message_type="text", text=text)

            ctx = await build_context(session, uid)
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
        except Exception as e:
            logger.error(f"Handler error: {e}", exc_info=True)
            await session.rollback()
            await message.answer("Что-то пошло не так. Попробуй ещё раз!")


# ═══════════════════════════════════════════════════════════════════════════════
# Photo handler
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(F.photo)
async def handle_photo(message: Message, bot: Bot) -> None:
    caption = message.caption.strip() if message.caption else None
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    async with async_session_factory() as session:
        try:
            ml = MealLogger(session)
            uid = await ml.ensure_user(telegram_id=message.from_user.id, username=message.from_user.username,
                                        first_name=message.from_user.first_name or "")
            photo_path = await download_telegram_photo(bot, message.photo[-1].file_id)
            if photo_path is None: await message.answer("Не удалось скачать фото."); return
            await ml.log_raw_message(user_id=uid, telegram_message_id=message.message_id,
                                      message_type="photo_with_caption" if caption else "photo",
                                      text=caption, photo_path=str(photo_path))

            # Build context + let the orchestrator (with vision) decide everything
            s = settings
            if not s.is_ai_configured or s.ai_provider not in ("gigachat",):
                await message.answer("Фото сохранил, но vision-модель пока не настроена. Опиши, что там было, и я запишу.")
                await session.commit(); return

            from app.services.context import build_context as build_ctx
            ctx = await build_ctx(session, uid)
            if message.reply_to_message and message.reply_to_message.text:
                ctx["reply_to"] = message.reply_to_message.text[:500]

            llm = _get_llm()
            # Use orchestrator WITH photo — same prompt, same context, plus the image
            if hasattr(llm, 'orchestrate_with_photo'):
                result = await llm.orchestrate_with_photo(caption or "Что на фото?", ctx, str(photo_path))
            else:
                result = await llm.orchestrate(caption or "Что на фото?", ctx)

            action = result.get("action", "unknown")
            logger.info(f"Photo orchestrator: action={action}")

            error = await _dispatch_action(action, result, ctx, session, ml, uid, message.from_user.id, caption or "фото еды", message)
            if error:
                await message.answer("Не понял что на фото. Опиши словами!")
            await session.commit()
        except Exception as e:
            logger.error(f"Photo handler error: {e}", exc_info=True)
            await session.rollback()
            await message.answer("Не удалось обработать фото. Попробуй ещё раз!")


# ═══════════════════════════════════════════════════════════════════════════════
# Voice handler
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(F.voice)
async def handle_voice(message: Message, bot: Bot) -> None:
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    voice_bytes = await download_telegram_voice(bot, message.voice.file_id)
    if voice_bytes is None: await message.answer("Не удалось скачать голосовое сообщение."); return

    from app.providers.speech import get_stt_provider
    stt = get_stt_provider()
    if stt is None: await message.answer("Распознавание речи не настроено. Напиши текстом!"); return

    text = await stt.transcribe(voice_bytes)
    if not text: await message.answer("Не удалось распознать речь. Напиши текстом!"); return

    await message.answer(f"🎤 *{text}*", parse_mode="Markdown")

    async with async_session_factory() as session:
        try:
            ml = MealLogger(session)
            uid = await ml.ensure_user(telegram_id=message.from_user.id, username=message.from_user.username,
                                        first_name=message.from_user.first_name or "")
            await ml.log_raw_message(user_id=uid, telegram_message_id=message.message_id, message_type="text", text=f"[voice] {text}")

            ctx = await build_context(session, uid)
            if message.reply_to_message and message.reply_to_message.text:
                ctx["reply_to"] = message.reply_to_message.text[:500]

            llm = _get_llm()
            result = await llm.orchestrate(text, ctx)
            logger.info(f"Voice orchestrator: action={result.get('action')}")

            error = await _dispatch_action(result.get("action", "unknown"), result, ctx, session, ml, uid, message.from_user.id, text, message)
            if error:
                await message.answer("Я не совсем понял. Расскажи, что ты съел!")

            await session.commit()
        except Exception as e:
            logger.error(f"Voice handler error: {e}", exc_info=True)
            await session.rollback()
            await message.answer("Что-то пошло не так. Попробуй ещё раз!")
