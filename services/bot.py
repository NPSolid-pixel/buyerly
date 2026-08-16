import asyncio
from pathlib import Path

from aiogram import Bot, Dispatcher

from bot.handlers import router as bot_router
from core.config import settings
from core.runtime import configure_logging


async def main() -> None:
    logger = configure_logging("bot")
    if not settings.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is required for the bot service")

    bot = Bot(token=settings.BOT_TOKEN)
    dispatcher = Dispatcher()
    dispatcher.include_router(bot_router)

    identity = await bot.get_me()
    Path("/tmp/buyerly-bot-ready").touch()
    logger.info("Telegram identity verified: @%s", identity.username or identity.id)
    logger.info("Starting Buyerly Telegram bot polling")
    try:
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
