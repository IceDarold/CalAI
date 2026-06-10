"""Telegram bot handlers — commands, text messages, photo messages."""

import logging

from aiogram import Bot, F, Router
from aiogram.types import Message
from aiogram.filters import Command, CommandStart

from app.config import settings
from app.db.database import async_session_factory
from app.schemas.intent import IntentType
from app.services.food_analyzer import FoodAnalyzer
from app.services.intent import IntentDetector
from app.services.meal_logger import MealLogger
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
/help — подсказки

Погнали! 💪"""

HELP_TEXT = """Как это работает:

1. Просто напиши, что съел — бот поймёт и запишет
2. Можешь уточнить порции: "150 г курицы, тарелка супа"
3. Присылай фото — если настроена vision-модель, бот проанализирует сам
4. /today — посмотреть итоги за сегодня

Бот даёт примерные оценки калорий и белка. Это не медицинский инструмент, а просто удобный трекер.

Если оценка неточная — просто уточни, и я перезапишу.

Примеры сообщений:
• съел курицу с рисом
• на обед была гречка, две котлеты и огурцы
• это был ужин
• сохрани это как перекус
• сегодня ел греческий йогурт с фруктами"""

UNKNOWN_TEXT = """Я не совсем понял. Расскажи, что ты съел, или напиши /help, чтобы посмотреть примеры."""


@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot) -> None:
    """Handle /start command."""
    await message.answer(START_TEXT)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Handle /help command."""
    await message.answer(HELP_TEXT)


@router.message(Command("today"))
async def cmd_today(message: Message) -> None:
    """Handle /today command — show today's meals."""
    async with async_session_factory() as session:
        meal_logger = MealLogger(session)
        user_id = await meal_logger.ensure_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name or "",
        )
        summary_service = SummaryService(session)
        summary = await summary_service.get_today_summary(user_id)
        await message.answer(summary)


@router.message(F.text)
async def handle_text(message: Message, bot: Bot) -> None:
    """Handle free-form text messages."""
    text = message.text.strip() if message.text else ""

    if not text:
        await message.answer(UNKNOWN_TEXT)
        return

    async with async_session_factory() as session:
        meal_logger = MealLogger(session)
        user_id = await meal_logger.ensure_user(
            telegram_id=message.from_user.id,
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
            await message.answer(summary)
        elif intent.intent == IntentType.HELP:
            await message.answer(HELP_TEXT)
        elif intent.intent == IntentType.LOG_MEAL:
            # Analyze food
            analyzer = FoodAnalyzer()
            analysis = await analyzer.analyze_text(text)

            # Save and respond
            _, response = await meal_logger.log_from_text(user_id, text, analysis)
            await message.answer(response)
        else:
            await message.answer(UNKNOWN_TEXT)

        await session.commit()


@router.message(F.photo)
async def handle_photo(message: Message, bot: Bot) -> None:
    """Handle photo messages (with or without caption)."""
    caption = message.caption.strip() if message.caption else None

    async with async_session_factory() as session:
        meal_logger = MealLogger(session)
        user_id = await meal_logger.ensure_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name or "",
        )

        # Download photo
        file_id = message.photo[-1].file_id  # largest size
        tg_file = await bot.get_file(file_id)

        photo_path = await download_telegram_photo(
            file_path=tg_file.file_path,
            bot_token=settings.telegram_bot_token,
        )

        if photo_path is None:
            await message.answer("Не удалось скачать фото. Попробуй ещё раз.")
            return

        # Save raw message
        await meal_logger.log_raw_message(
            user_id=user_id,
            telegram_message_id=message.message_id,
            message_type="photo_with_caption" if caption else "photo",
            text=caption,
            photo_path=str(photo_path),
        )

        # Analyze photo
        analyzer = FoodAnalyzer()

        if not analyzer.has_vision:
            # Vision not configured — save photo, ask for text
            await message.answer(
                "Фото сохранил, но vision-модель пока не настроена. "
                "Опиши, что там было, и я запишу калории."
            )
            await session.commit()
            return

        # Vision is available — analyze
        analysis = await analyzer.analyze_photo(str(photo_path), caption)
        _, response = await meal_logger.log_from_photo(
            user_id, str(photo_path), caption, analysis
        )
        await message.answer(response)
        await session.commit()
