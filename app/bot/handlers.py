"""Telegram bot handlers — commands, text messages, photo messages."""

import asyncio
import logging
import re

from aiogram import Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.types import Message
from aiogram.filters import Command, CommandStart

from app.config import settings
from app.db.database import async_session_factory
from app.schemas.intent import IntentType
from app.services.food_analyzer import FoodAnalyzer
from app.services.intent import IntentDetector
from app.services.meal_logger import (
    MealLogger,
    get_last_meal,
    set_last_meal,
    clear_last_meal,
)
from app.services.summary import SummaryService
from app.utils.files import download_telegram_photo

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

1. Просто напиши, что съел — бот поймёт и запишет
2. Можешь уточнить порции: "150 г курицы, тарелка супа"
3. Присылай фото — если настроена vision-модель, бот проанализирует сам
4. /today — посмотреть итоги за сегодня
5. /undo — удалить последнюю запись
6. /delete 3 — удалить конкретную запись
7. Напиши "и ещё X" — добавится к последнему приёму пищи

Бот даёт примерные оценки калорий и белка. Это не медицинский инструмент, а просто удобный трекер.

Если оценка неточная — просто уточни граммы, и я посчитаю точнее."""

UNKNOWN_TEXT = """Я не совсем понял. Расскажи, что ты съел, или напиши /help, чтобы посмотреть примеры."""

# ── "и ещё" detection ──────────────────────────────────────────────────────
_AND_MORE_PATTERN = re.compile(
    r'^\s*(?:и\s+)?(?:ещ[ёе]|плюс|также|добав(?:ь|ить)|вдогонку)\b',
    re.IGNORECASE,
)


def _is_continuation(text: str) -> bool:
    """Check if the message is a continuation of the previous meal."""
    return bool(_AND_MORE_PATTERN.match(text))


def _strip_continuation_prefix(text: str) -> str:
    """Remove 'и ещё' / 'плюс' prefix from the message."""
    return _AND_MORE_PATTERN.sub('', text).strip()


async def send_animated(message: Message, text: str, chunk_delay: float = 0.08) -> None:
    """Send a message with progressive text-reveal animation."""
    words = text.split()
    if len(words) <= 6:
        await message.answer(text)
        return

    chunk_size = 3
    chunks: list[str] = []
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
        summary_service = SummaryService(session)
        summary = await summary_service.get_today_summary(user_id)

        # Add draft reminder if there are drafts
        draft_count = await meal_logger.get_draft_count(user_id)
        if draft_count > 0:
            summary += (
                f"\n\n⚠️ У тебя {draft_count} незавершённ{'ая' if draft_count == 1 else 'ые' if draft_count < 5 else 'ых'} "
                f"запис{'ь' if draft_count == 1 else 'и' if draft_count < 5 else 'ей'} с низкой уверенностью. "
                f"Хочешь уточнить? Напиши номер приёма и уточни детали."
            )

        await send_animated(message, summary)


@router.message(Command("undo"))
async def cmd_undo(message: Message, bot: Bot) -> None:
    """Delete the last meal."""
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
            await message.answer(f"Удалил: {desc}.")
        else:
            await message.answer("Нечего удалять — сегодня ещё нет записей.")

        await session.commit()


@router.message(Command("delete"))
async def cmd_delete(message: Message, bot: Bot) -> None:
    """Delete a specific meal by number from /today."""
    # Parse number from message
    args = message.text.strip().split()
    if len(args) < 2 or not args[1].isdigit():
        await message.answer("Напиши номер: /delete 3 (номер из списка в /today)")
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
            await message.answer(f"Удалил запись #{n}: {desc}.")
        else:
            await message.answer(f"Запись #{n} не найдена. Проверь номер в /today.")

        await session.commit()


# ── Text handler ────────────────────────────────────────────────────────────

@router.message(F.text)
async def handle_text(message: Message, bot: Bot) -> None:
    text = message.text.strip() if message.text else ""

    if not text:
        await message.answer(UNKNOWN_TEXT)
        return

    telegram_id = message.from_user.id

    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    # ── Check if this is a continuation ("и ещё яблоко") ──
    is_continuation = _is_continuation(text)
    if is_continuation:
        last_meal_id = get_last_meal(telegram_id)
        if last_meal_id is not None:
            continuation_text = _strip_continuation_prefix(text)
            if not continuation_text:
                continuation_text = text  # fallback to original

            async with async_session_factory() as session:
                meal_logger = MealLogger(session)
                user_id = await meal_logger.ensure_user(
                    telegram_id=telegram_id,
                    username=message.from_user.username,
                    first_name=message.from_user.first_name or "",
                )

                # Analyze the continuation as food
                analyzer = FoodAnalyzer()
                analysis = await analyzer.analyze_text(continuation_text, session=session)

                if not analysis.is_food:
                    await message.answer("Не понял что добавить. Опиши продукт.")
                    return

                _, response = await meal_logger.append_to_meal(
                    last_meal_id, continuation_text, analysis
                )
                await send_animated(message, response)
                await session.commit()
                return
        # If no last meal, fall through — treat as new meal

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

        # Detect intent
        intent_detector = IntentDetector()
        intent = await intent_detector.detect(text)

        logger.info(f"Intent: {intent.intent.value} (confidence={intent.confidence}) for text: {text[:100]}")

        if intent.intent == IntentType.SHOW_TODAY:
            summary_service = SummaryService(session)
            summary = await summary_service.get_today_summary(user_id)
            draft_count = await meal_logger.get_draft_count(user_id)
            if draft_count > 0:
                ending = (
                    ("ая запись", "и", "ь")
                    if draft_count == 1
                    else ("ые записи", "и", "и")
                    if draft_count < 5
                    else ("ых записей", "ей", "и")
                )
                summary += (
                    f"\n\n⚠️ У тебя {draft_count} незавершённ{ending[0]} с низкой уверенностью. "
                    f"Хочешь уточн{ending[1]}?"
                )
            await send_animated(message, summary)
        elif intent.intent == IntentType.HELP:
            await send_animated(message, HELP_TEXT)
        elif intent.intent == IntentType.LOG_MEAL:
            analyzer = FoodAnalyzer()
            analysis = await analyzer.analyze_text(text, session=session)

            meal, response = await meal_logger.log_from_text(user_id, text, analysis)

            # Remember this meal for "и ещё" chaining
            if meal:
                set_last_meal(telegram_id, meal.id)

            await send_animated(message, response)
        else:
            await message.answer(UNKNOWN_TEXT)

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
