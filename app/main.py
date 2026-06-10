"""Entry point — creates bot, registers handlers, starts polling."""

import asyncio
import logging

from aiogram import Bot, Dispatcher

from app.bot.handlers import router
from app.config import settings
from app.db.database import init_db

logger = logging.getLogger(__name__)


async def main() -> None:
    """Start the bot."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if not settings.telegram_bot_token:
        logger.error("TELEGRAM_BOT_TOKEN is not set. Create a .env file and set it.")
        return

    # Initialize database
    await init_db()
    logger.info("Database initialized.")

    # Create bot and dispatcher
    bot = Bot(token=settings.telegram_bot_token)
    dp = Dispatcher()
    dp.include_router(router)

    logger.info("Bot started. Polling...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
