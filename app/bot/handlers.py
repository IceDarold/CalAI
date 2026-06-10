"""Telegram bot handlers — LLM decides everything, bot just executes."""

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.types import Message
from aiogram.filters import Command, CommandStart

from app.config import settings
from app.db.database import async_session_factory
from app.services.food_analyzer import FoodAnalyzer
from app.services.meal_logger import (
    MealLogger, get_last_meal, set_last_meal, clear_last_meal,
)
from app.services.summary import SummaryService
from app.utils.files import download_telegram_photo
from app.utils.time import format_meal_type

logger = logging.getLogger(__name__)
router = Router()

START_TEXT = """Привет! 👋 Я твой трекер питания.

Просто пиши мне, что ты съел, в свободной форме — я посчитаю калории и белок.

Примеры:
• "съел курицу с рисом и салатом"
• "на обед гречка, две котлеты и огурцы"
• "перекусил йогуртом с фруктами"

Можешь прислать фото еды — я попробую проанализировать.

Команды:
/today — что я съел сегодня
/undo — удалить последнюю запись
/delete 3 — удалить запись номер 3 из сегодня
/help — подсказки

Если хочешь добавить что-то к последнему приёму — просто напиши "и ещё яблоко".
Если хочешь исправить — "нет, там было 200 грамм риса". Я пойму 😉

Погнали! 💪"""

HELP_TEXT = """Как это работает:

1. Просто напиши, что съел — я всё пойму и запишу
2. Можешь уточнить порции: "150 г курицы, тарелка супа"
3. "и ещё X" — добавится к последнему приёму
4. "нет, там было X" — исправлю последнюю запись
5. /today — посмотреть итоги за сегодня
6. /undo — удалить последнюю запись
7. /delete 3 — удалить конкретную запись

Я использую базу USDA для точных цифр. Если оценка неточная — уточни граммы, и я пересчитаю."""


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _build_context(session, user_id: int, telegram_id: int) -> dict:
    from app.db.models import Meal, RawMessage
    from sqlalchemy import select
    from app.db.repositories import get_today_meals, get_today_totals

    ctx: dict = {}

    meals = await get_today_meals(session, user_id)
    totals = await get_today_totals(session, user_id)
    ctx["meals_today"] = []
    for m in meals:
        items = [{"name": it.name, "grams": f"{it.calories_min or '?'}"} for it in m.items]
        ctx["meals_today"].append({
            "meal_type": format_meal_type(m.meal_type),
            "items": items,
            "calories": f"{m.calories_min}–{m.calories_max} ккал" if m.calories_min else "?",
            "status": m.status,
        })
    ctx["totals_today"] = {
        "calories": f"{totals.get('calories_min', 0)}–{totals.get('calories_max', 0)} ккал",
        "protein": f"{totals.get('protein_min_g', 0):.0f}–{totals.get('protein_max_g', 0):.0f} г",
    }

    last_id = get_last_meal(telegram_id)
    if last_id:
        meal = await session.get(Meal, last_id)
        if meal:
            ctx["last_meal"] = {
                "meal_type": format_meal_type(meal.meal_type),
                "items": [{"name_ru": it.name, "grams": f"{it.calories_min or '?'} г"} for it in meal.items],
                "calories": f"{meal.calories_min}–{meal.calories_max} ккал" if meal.calories_min else "?",
                "original_text": meal.original_text or "",
            }

    result = await session.execute(
        select(RawMessage).where(RawMessage.user_id == user_id).order_by(RawMessage.created_at.desc()).limit(10)
    )
    recent = list(result.scalars())
    recent.reverse()
    ctx["history"] = [{"role": "user", "text": rm.text or "(фото)"} for rm in recent]

    return ctx


def _inject_numbers(text: str, cal_min: int, cal_max: int, prot_min: float, prot_max: float,
                    confidence: str) -> str:
    cal = f"{cal_min}–{cal_max} ккал"
    prot = f"{prot_min:.0f}–{prot_max:.0f} г белка"
    note = "" if confidence == "high" else "Это диапазон — точный вес неизвестен. Уточни граммы, и я посчитаю точнее."
    return text.replace("{{CALORIES}}", cal).replace("{{PROTEIN}}", prot).replace("{{RANGE_NOTE}}", note)


def _format_today(meals, totals) -> str:
    if not meals:
        return "Сегодня ты ещё ничего не записал. Напиши, что съел, и я посчитаю!"

    lines = ["Сегодня:\n"]
    for i, meal in enumerate(meals, 1):
        mt = format_meal_type(meal.meal_type)
        items_str = ", ".join(it.name for it in meal.items) if meal.items else "—"
        cal = f"{meal.calories_min}–{meal.calories_max} ккал" if meal.calories_min else ""
        prot = f"{meal.protein_min_g:.0f}–{meal.protein_max_g:.0f} г белка" if meal.protein_min_g is not None else ""
        details = f" — {cal}, {prot}" if cal else ""
        lines.append(f"{i}. {mt.capitalize()} — {items_str}{details}")

    if totals.get("meal_count", 0) > 0:
        lines.append(f"\nИтого: {totals['calories_min']}–{totals['calories_max']} ккал, "
                     f"{totals['protein_min_g']:.0f}–{totals['protein_max_g']:.0f} г белка.")
    return "\n".join(lines)


async def send_animated(message: Message, text: str, chunk_delay: float = 0.06) -> None:
    words = text.split()
    if len(words) <= 8:
        await message.answer(text)
        return
    chunk_size = 3
    chunks = []
    for i in range(0, len(words), chunk_size):
        chunk = " ".join(words[i : i + chunk_size])
        chunks.append(chunks[-1] + " " + chunk if chunks else chunk)
    sent = await message.answer(chunks[0] + " ▌")
    for c in chunks[1:]:
        await asyncio.sleep(chunk_delay)
        try:
            await sent.edit_text(c + " ▌")
        except Exception:
            pass
    await asyncio.sleep(chunk_delay * 2)
    try:
        await sent.edit_text(text)
    except Exception:
        pass


# ── Shared pipeline: LLM items → USDA search → calculator → FoodAnalysis ────

async def _run_nutrition_pipeline(session, result: dict):
    """Parse LLM items, search USDA, calculate nutrition, return (items, parsed, analysis)."""
    from app.schemas.food import ParsedFoodItem, Confidence as C, FoodAnalysis as FA, FoodItem as FI, MealType as MT
    from app.services.food_db import search_food
    from app.services.calculator import calculate_from_parsed

    parsed = [
        ParsedFoodItem(
            name_ru=it.get("name_ru", it.get("name", "")),
            name_en=it.get("name_en", it.get("name", "")),
            grams=float(it.get("grams", 100)),
            grams_confidence=it.get("grams_confidence", "medium"),
            portion_text=it.get("portion_text", ""),
        )
        for it in result.get("items", [])
    ]

    food_matches = []
    for pi in parsed:
        matches = await search_food(session, pi.name_en or pi.name_ru, limit=1)
        food_matches.append(matches[0] if matches else None)

    parsed_dicts = [p.model_dump() for p in parsed]
    calc = calculate_from_parsed(parsed_dicts, food_matches)

    conf = C(result.get("confidence", "medium"))
    meal_type = MT(result.get("meal_type", "unknown"))

    items = [
        FI(name=pi.name_ru or pi.name_en, portion_text=pi.portion_text or f"~{ci.grams:.0f} г",
           calories_min=int(ci.kcal * 0.85), calories_max=int(ci.kcal * 1.15),
           protein_min_g=round(ci.protein_g * 0.85, 1), protein_max_g=round(ci.protein_g * 1.15, 1),
           confidence=C(ci.confidence))
        for pi, ci in zip(parsed, calc.items)
    ]

    analysis = FA(
        is_food=True, meal_type=meal_type, items=items,
        total_calories_min=round(calc.total_kcal * 0.85),
        total_calories_max=round(calc.total_kcal * 1.15),
        total_protein_min_g=round(calc.total_protein_g * 0.85, 1),
        total_protein_max_g=round(calc.total_protein_g * 1.15, 1),
        confidence=conf, parsed_items=parsed,
    )

    return items, parsed, analysis


# ── Command handlers ────────────────────────────────────────────────────────

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
        meal_logger = MealLogger(session)
        user_id = await meal_logger.ensure_user(
            telegram_id=message.from_user.id, username=message.from_user.username,
            first_name=message.from_user.first_name or "")
        from app.db.repositories import get_today_meals, get_today_totals
        meals = await get_today_meals(session, user_id)
        totals = await get_today_totals(session, user_id)
        await send_animated(message, _format_today(meals, totals))

@router.message(Command("undo"))
async def cmd_undo(message: Message, bot: Bot) -> None:
    async with async_session_factory() as session:
        meal_logger = MealLogger(session)
        user_id = await meal_logger.ensure_user(
            telegram_id=message.from_user.id, username=message.from_user.username,
            first_name=message.from_user.first_name or "")
        desc = await meal_logger.delete_last_meal(user_id)
        if desc:
            clear_last_meal(message.from_user.id)
            await message.answer(f"Убрал: {desc}.")
        else:
            await message.answer("Нечего удалять.")
        await session.commit()

@router.message(Command("delete"))
async def cmd_delete(message: Message, bot: Bot) -> None:
    args = message.text.strip().split()
    if len(args) < 2 or not args[1].isdigit():
        await message.answer("Напиши номер: /delete 3 (номер из /today)")
        return
    n = int(args[1])
    async with async_session_factory() as session:
        meal_logger = MealLogger(session)
        user_id = await meal_logger.ensure_user(
            telegram_id=message.from_user.id, username=message.from_user.username,
            first_name=message.from_user.first_name or "")
        desc = await meal_logger.delete_meal_by_number(user_id, n)
        await message.answer(f"Убрал запись #{n}: {desc}." if desc else f"Запись #{n} не найдена.")
        await session.commit()


# ── Main text handler — LLM decides, bot executes ───────────────────────────

@router.message(F.text)
async def handle_text(message: Message, bot: Bot) -> None:
    text = message.text.strip() if message.text else ""
    if not text:
        await message.answer("Расскажи, что ты съел!")
        return

    telegram_id = message.from_user.id
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    async with async_session_factory() as session:
        meal_logger = MealLogger(session)
        user_id = await meal_logger.ensure_user(
            telegram_id=telegram_id, username=message.from_user.username,
            first_name=message.from_user.first_name or "")

        await meal_logger.log_raw_message(user_id=user_id, telegram_message_id=message.message_id,
                                          message_type="text", text=text)

        ctx = await _build_context(session, user_id, telegram_id)

        # ── LLM decision ──
        from app.providers.yandex import YandexGPTProvider
        llm = YandexGPTProvider()
        result = await llm.orchestrate(text, ctx)

        action = result.get("action", "unknown")
        llm_response = result.get("response_text", "")
        llm_confidence = result.get("confidence", "medium")
        logger.info(f"Orchestrator: action={action} confidence={llm_confidence}")

        # ── Execute action ──

        if action == "show_today":
            from app.db.repositories import get_today_meals, get_today_totals
            meals = await get_today_meals(session, user_id)
            totals = await get_today_totals(session, user_id)
            await send_animated(message, _format_today(meals, totals))
            await session.commit(); return

        if action == "delete_last":
            desc = await meal_logger.delete_last_meal(user_id)
            clear_last_meal(telegram_id)
            await message.answer(llm_response or (f"Убрал: {desc}." if desc else "Нечего удалять."))
            await session.commit(); return

        if action == "help":
            await send_animated(message, HELP_TEXT)
            await session.commit(); return

        if action == "unknown":
            await message.answer(llm_response or "Я не совсем понял. Расскажи, что ты съел!")
            await session.commit(); return

        if action == "log_meal":
            items, parsed, analysis = await _run_nutrition_pipeline(session, result)

            if not parsed:
                await message.answer("Не смог разобрать что за еда. Опиши подробнее!")
                await session.commit(); return

            meal, _ = await meal_logger.log_from_text(user_id, text, analysis)
            if meal:
                set_last_meal(telegram_id, meal.id)

            items_str = ", ".join(it.name for it in items)
            response = _inject_numbers(
                llm_response or f"Записал как {format_meal_type(analysis.meal_type.value)}: {{ITEMS}}. {{CALORIES}} {{RANGE_NOTE}}",
                analysis.total_calories_min, analysis.total_calories_max,
                analysis.total_protein_min_g, analysis.total_protein_max_g, llm_confidence,
            ).replace("{{ITEMS}}", items_str)

            await send_animated(message, response)
            await session.commit(); return

        if action == "append_meal":
            last_meal_id = get_last_meal(telegram_id)
            if last_meal_id is None:
                await message.answer("Не к чему добавлять. Расскажи, что съел, и я запишу как новый приём.")
                await session.commit(); return

            items, parsed, analysis = await _run_nutrition_pipeline(session, result)
            if not parsed:
                await message.answer("Не понял что добавить. Опиши продукт.")
                await session.commit(); return

            meal, _ = await meal_logger.append_to_meal(last_meal_id, text, analysis)
            if meal:
                # Recalculate totals after append
                all_cal_min = sum(it.calories_min or 0 for it in meal.items)
                all_cal_max = sum(it.calories_max or 0 for it in meal.items)
                all_prot_min = sum(it.protein_min_g or 0 for it in meal.items)
                all_prot_max = sum(it.protein_max_g or 0 for it in meal.items)
                analysis.total_calories_min = all_cal_min
                analysis.total_calories_max = all_cal_max
                analysis.total_protein_min_g = all_prot_min
                analysis.total_protein_max_g = all_prot_max

            response = _inject_numbers(
                llm_response or f"Добавил к приёму. {{CALORIES}} {{RANGE_NOTE}}",
                analysis.total_calories_min, analysis.total_calories_max,
                analysis.total_protein_min_g, analysis.total_protein_max_g, llm_confidence,
            )
            await send_animated(message, response)
            await session.commit(); return

        if action == "update_meal":
            last_meal_id = get_last_meal(telegram_id)
            if last_meal_id is None:
                await message.answer("Нечего исправлять. Расскажи, что съел, и я запишу.")
                await session.commit(); return

            items, parsed, analysis = await _run_nutrition_pipeline(session, result)
            if not parsed:
                await message.answer("Не понял что исправить. Опиши точнее.")
                await session.commit(); return

            meal, _ = await meal_logger.update_meal(last_meal_id, text, analysis)

            response = _inject_numbers(
                llm_response or f"Исправил запись. {{CALORIES}} {{RANGE_NOTE}}",
                analysis.total_calories_min, analysis.total_calories_max,
                analysis.total_protein_min_g, analysis.total_protein_max_g, llm_confidence,
            )
            await send_animated(message, response)
            await session.commit(); return

        # Fallback
        await message.answer("Я не совсем понял. Расскажи, что ты съел!")
        await session.commit()


# ── Photo handler ───────────────────────────────────────────────────────────

@router.message(F.photo)
async def handle_photo(message: Message, bot: Bot) -> None:
    caption = message.caption.strip() if message.caption else None
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    async with async_session_factory() as session:
        meal_logger = MealLogger(session)
        user_id = await meal_logger.ensure_user(
            telegram_id=message.from_user.id, username=message.from_user.username,
            first_name=message.from_user.first_name or "")

        file_id = message.photo[-1].file_id
        tg_file = await bot.get_file(file_id)
        photo_path = await download_telegram_photo(file_path=tg_file.file_path,
                                                    bot_token=settings.telegram_bot_token)

        if photo_path is None:
            await message.answer("Не удалось скачать фото. Попробуй ещё раз.")
            return

        await meal_logger.log_raw_message(user_id=user_id, telegram_message_id=message.message_id,
                                          message_type="photo_with_caption" if caption else "photo",
                                          text=caption, photo_path=str(photo_path))

        analyzer = FoodAnalyzer()
        if not analyzer.has_vision:
            await message.answer("Фото сохранил, но vision-модель пока не настроена. Опиши, что там было, и я запишу калории.")
            await session.commit(); return

        analysis = await analyzer.analyze_photo(str(photo_path), caption)
        meal, _ = await meal_logger.log_from_photo(user_id, str(photo_path), caption, analysis)
        if meal:
            set_last_meal(message.from_user.id, meal.id)
        await send_animated(message, "Записал! (анализ фото пока в тестовом режиме)")
        await session.commit()
