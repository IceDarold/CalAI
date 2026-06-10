"""Telegram bot handlers — LLM-driven, no hardcoded logic.

Flow:
1. User sends message
2. Bot builds context (last meal, drafts, today summary)
3. LLM decides: action + parsed items + natural response text
4. Bot executes action (DB) + replaces {{PLACEHOLDERS}} with real numbers
5. Bot sends the finalized response
"""

import asyncio
import logging
import re

from aiogram import Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.types import Message
from aiogram.filters import Command, CommandStart

from app.config import settings
from app.db.database import async_session_factory
from app.services.food_analyzer import FoodAnalyzer
from app.services.meal_logger import (
    MealLogger,
    get_last_meal,
    set_last_meal,
    clear_last_meal,
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

Кстати, если хочешь добавить что-то к последнему приёму пищи — просто напиши "и ещё яблоко". Я пойму 😉

Погнали! 💪"""

HELP_TEXT = """Как это работает:

1. Просто напиши, что съел — я всё пойму и запишу
2. Можешь уточнить порции: "150 г курицы, тарелка супа"
3. Напиши "и ещё X" — добавится к последнему приёму
4. /today — посмотреть итоги за сегодня
5. /undo — удалить последнюю запись
6. /delete 3 — удалить конкретную запись

Я использую базу USDA для точных цифр по калориям и белку. Если оценка неточная — уточни граммы, и я пересчитаю точнее."""


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _build_context(session, user_id: int, telegram_id: int) -> dict:
    """Build context dict for the LLM orchestrator."""
    ctx: dict = {}

    # Last meal
    last_id = get_last_meal(telegram_id)
    if last_id:
        from app.db.models import Meal
        meal = await session.get(Meal, last_id)
        if meal:
            ctx["last_meal"] = {
                "meal_type": format_meal_type(meal.meal_type),
                "items": [
                    {"name_ru": it.name, "grams": it.calories_min}  # approximate
                    for it in meal.items
                ],
            }

    # Draft count
    from app.db.models import Meal
    from sqlalchemy import select
    result = await session.execute(
        select(Meal).where(Meal.user_id == user_id, Meal.status == "draft")
    )
    ctx["draft_count"] = len(result.scalars().all())

    return ctx


def _inject_numbers(response_text: str, calories_min: int, calories_max: int,
                    protein_min: float, protein_max: float, confidence: str) -> str:
    """Replace {{PLACEHOLDERS}} in LLM response with real calculated values."""
    cal_range = f"{calories_min}–{calories_max} ккал"
    prot_range = f"{protein_min:.0f}–{protein_max:.0f} г белка"

    if confidence == "high":
        range_note = ""
    else:
        range_note = "Это диапазон — точный вес неизвестен. Уточни граммы, и я посчитаю точнее."

    text = response_text.replace("{{CALORIES}}", cal_range)
    text = text.replace("{{PROTEIN}}", prot_range)
    text = text.replace("{{RANGE_NOTE}}", range_note)
    return text


def _format_today_response(meals: list, totals: dict, draft_count: int) -> str:
    """Format /today response. This one stays deterministic because it's data."""
    if not meals:
        return "Сегодня ты ещё ничего не записал. Напиши, что съел, и я посчитаю!"

    lines = ["Сегодня:\n"]
    for i, meal in enumerate(meals, 1):
        mt = format_meal_type(meal.meal_type)
        items_str = ", ".join(it.name for it in meal.items) if meal.items else "—"
        cal = f"{meal.calories_min}–{meal.calories_max} ккал" if meal.calories_min else ""
        prot = f"{meal.protein_min_g:.0f}–{meal.protein_max_g:.0f} г белка" if meal.protein_min_g is not None else ""
        draft_marker = " ⚠️" if meal.status == "draft" else ""
        details = f" — {cal}, {prot}" if cal else ""
        lines.append(f"{i}. {mt.capitalize()}{draft_marker} — {items_str}{details}")

    if totals.get("meal_count", 0) > 0:
        lines.append(
            f"\nИтого: {totals['calories_min']}–{totals['calories_max']} ккал, "
            f"{totals['protein_min_g']:.0f}–{totals['protein_max_g']:.0f} г белка."
        )

    if draft_count > 0:
        lines.append(
            f"\n⚠️ {draft_count} незавершённых записей с низкой уверенностью. Хочешь уточнить?"
        )

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
        if i == 0:
            chunks.append(chunk)
        else:
            chunks.append(chunks[-1] + " " + chunk)

    sent_msg = await message.answer(chunks[0] + " ▌")
    for chunk_text in chunks[1:]:
        await asyncio.sleep(chunk_delay)
        try:
            await sent_msg.edit_text(chunk_text + " ▌")
        except Exception:
            pass
    await asyncio.sleep(chunk_delay * 2)
    try:
        await sent_msg.edit_text(text)
    except Exception:
        pass


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
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name or "",
        )

        from app.db.repositories import get_today_meals, get_today_totals
        meals = await get_today_meals(session, user_id)
        totals = await get_today_totals(session, user_id)
        draft_count = await meal_logger.get_draft_count(user_id)

        response = _format_today_response(meals, totals, draft_count)
        await send_animated(message, response)

    await session.commit()


@router.message(Command("undo"))
async def cmd_undo(message: Message, bot: Bot) -> None:
    async with async_session_factory() as session:
        meal_logger = MealLogger(session)
        user_id = await meal_logger.ensure_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name or "",
        )
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
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name or "",
        )
        desc = await meal_logger.delete_meal_by_number(user_id, n)
        if desc:
            await message.answer(f"Убрал запись #{n}: {desc}.")
        else:
            await message.answer(f"Запись #{n} не найдена. Проверь номер в /today.")
        await session.commit()


# ── Main text handler — LLM decides everything ──────────────────────────────

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
            telegram_id=telegram_id,
            username=message.from_user.username,
            first_name=message.from_user.first_name or "",
        )

        # Save raw message
        await meal_logger.log_raw_message(
            user_id=user_id,
            telegram_message_id=message.message_id,
            message_type="text",
            text=text,
        )

        # Build context for LLM
        ctx = await _build_context(session, user_id, telegram_id)

        # ── LLM decides everything ──
        from app.providers.yandex import YandexGPTProvider
        llm = YandexGPTProvider()
        result = await llm.orchestrate(text, ctx)

        action = result.get("action", "unknown")
        llm_response = result.get("response_text", "")
        llm_confidence = result.get("confidence", "medium")
        logger.info(f"Orchestrator: action={action} confidence={llm_confidence}")

        if action == "show_today":
            from app.db.repositories import get_today_meals, get_today_totals
            meals = await get_today_meals(session, user_id)
            totals = await get_today_totals(session, user_id)
            draft_count = await meal_logger.get_draft_count(user_id)
            response = _format_today_response(meals, totals, draft_count)
            await send_animated(message, response)
            await session.commit()
            return

        if action == "delete_last":
            desc = await meal_logger.delete_last_meal(user_id)
            clear_last_meal(telegram_id)
            await message.answer(
                llm_response or f"Убрал последнюю запись: {desc}."
                if desc else "Нечего удалять."
            )
            await session.commit()
            return

        if action == "help":
            await send_animated(message, HELP_TEXT)
            await session.commit()
            return

        if action == "unknown":
            await message.answer(
                llm_response or "Я не совсем понял. Расскажи, что ты съел!"
            )
            await session.commit()
            return

        if action == "log_meal":
            # Parse items from LLM, run USDA lookup + calculator
            from app.schemas.food import ParsedFoodItem
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

            if not parsed:
                await message.answer("Не смог разобрать что за еда. Опиши подробнее!")
                await session.commit()
                return

            # USDA lookup + calculator
            from app.services.food_db import search_food
            from app.services.calculator import calculate_from_parsed

            food_matches = []
            for pi in parsed:
                matches = await search_food(session, pi.name_en or pi.name_ru, limit=1)
                food_matches.append(matches[0] if matches else None)

            parsed_dicts = [p.model_dump() for p in parsed]
            calc = calculate_from_parsed(parsed_dicts, food_matches)

            # Build FoodAnalysis for saving
            meal_type_str = result.get("meal_type", "unknown")
            from app.schemas.food import Confidence as C, FoodAnalysis as FA, FoodItem as FI, MealType as MT

            food_items = [
                FI(
                    name=pi.name_ru or pi.name_en,
                    portion_text=pi.portion_text or f"~{ci.grams:.0f} г",
                    calories_min=int(ci.kcal * 0.85),
                    calories_max=int(ci.kcal * 1.15),
                    protein_min_g=round(ci.protein_g * 0.85, 1),
                    protein_max_g=round(ci.protein_g * 1.15, 1),
                    confidence=C(ci.confidence),
                )
                for pi, ci in zip(parsed, calc.items)
            ]

            analysis = FA(
                is_food=True,
                meal_type=MT(meal_type_str),
                items=food_items,
                total_calories_min=round(calc.total_kcal * 0.85),
                total_calories_max=round(calc.total_kcal * 1.15),
                total_protein_min_g=round(calc.total_protein_g * 0.85, 1),
                total_protein_max_g=round(calc.total_protein_g * 1.15, 1),
                confidence=C(llm_confidence),
                parsed_items=parsed,
            )

            meal, _ = await meal_logger.log_from_text(user_id, text, analysis)

            # Remember for "append" chaining
            if meal:
                set_last_meal(telegram_id, meal.id)

            # Format response with real numbers
            if llm_response:
                response = _inject_numbers(
                    llm_response,
                    analysis.total_calories_min,
                    analysis.total_calories_max,
                    analysis.total_protein_min_g,
                    analysis.total_protein_max_g,
                    llm_confidence,
                )
            else:
                items_str = ", ".join(it.name for it in food_items)
                response = (
                    f"Записал как {format_meal_type(meal_type_str)}: {items_str}.\n"
                    f"Оценка: {analysis.total_calories_min}–{analysis.total_calories_max} ккал, "
                    f"белок {analysis.total_protein_min_g:.0f}–{analysis.total_protein_max_g:.0f} г.\n"
                    f"Это диапазон — точный вес неизвестен. Уточни граммы, и я посчитаю точнее."
                )

            await send_animated(message, response)
            await session.commit()
            return

        if action == "append_meal":
            last_meal_id = get_last_meal(telegram_id)
            if last_meal_id is None:
                # No last meal — fall back to log_meal
                await message.answer("Не к чему добавлять. Расскажи, что съел, и я запишу как новый приём.")
                await session.commit()
                return

            # Parse items, lookup, calculate
            from app.schemas.food import ParsedFoodItem
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

            from app.services.food_db import search_food
            from app.services.calculator import calculate_from_parsed

            food_matches = []
            for pi in parsed:
                matches = await search_food(session, pi.name_en or pi.name_ru, limit=1)
                food_matches.append(matches[0] if matches else None)

            parsed_dicts = [p.model_dump() for p in parsed]
            calc = calculate_from_parsed(parsed_dicts, food_matches)

            from app.schemas.food import Confidence as C, FoodAnalysis as FA, FoodItem as FI, MealType as MT

            food_items = [
                FI(
                    name=pi.name_ru or pi.name_en,
                    portion_text=pi.portion_text or f"~{ci.grams:.0f} г",
                    calories_min=int(ci.kcal * 0.85),
                    calories_max=int(ci.kcal * 1.15),
                    protein_min_g=round(ci.protein_g * 0.85, 1),
                    protein_max_g=round(ci.protein_g * 1.15, 1),
                    confidence=C(ci.confidence),
                )
                for pi, ci in zip(parsed, calc.items)
            ]

            analysis = FA(
                is_food=True,
                meal_type=MT(result.get("meal_type", "unknown")),
                items=food_items,
                total_calories_min=round(calc.total_kcal * 0.85),
                total_calories_max=round(calc.total_kcal * 1.15),
                total_protein_min_g=round(calc.total_protein_g * 0.85, 1),
                total_protein_max_g=round(calc.total_protein_g * 1.15, 1),
                confidence=C(llm_confidence),
                parsed_items=parsed,
            )

            _, response = await meal_logger.append_to_meal(last_meal_id, text, analysis)

            # Inject numbers into LLM response
            if llm_response and "{{CALORIES}}" in llm_response:
                response = _inject_numbers(
                    llm_response,
                    analysis.total_calories_min,
                    analysis.total_calories_max,
                    analysis.total_protein_min_g,
                    analysis.total_protein_max_g,
                    llm_confidence,
                )

            await send_animated(message, response)
            await session.commit()
            return

        # Fallback
        await message.answer("Не понял. Расскажи, что ты съел!")
        await session.commit()


# ── Photo handler ───────────────────────────────────────────────────────────

@router.message(F.photo)
async def handle_photo(message: Message, bot: Bot) -> None:
    caption = message.caption.strip() if message.caption else None
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    async with async_session_factory() as session:
        meal_logger = MealLogger(session)
        user_id = await meal_logger.ensure_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name or "",
        )

        file_id = message.photo[-1].file_id
        tg_file = await bot.get_file(file_id)
        photo_path = await download_telegram_photo(
            file_path=tg_file.file_path,
            bot_token=settings.telegram_bot_token,
        )

        if photo_path is None:
            await message.answer("Не удалось скачать фото. Попробуй ещё раз.")
            return

        await meal_logger.log_raw_message(
            user_id=user_id,
            telegram_message_id=message.message_id,
            message_type="photo_with_caption" if caption else "photo",
            text=caption,
            photo_path=str(photo_path),
        )

        analyzer = FoodAnalyzer()
        if not analyzer.has_vision:
            await message.answer(
                "Фото сохранил, но vision-модель пока не настроена. "
                "Опиши, что там было, и я запишу калории."
            )
            await session.commit()
            return

        analysis = await analyzer.analyze_photo(str(photo_path), caption)
        meal, response = await meal_logger.log_from_photo(
            user_id, str(photo_path), caption, analysis
        )
        if meal:
            set_last_meal(message.from_user.id, meal.id)
        await send_animated(message, response)
        await session.commit()
