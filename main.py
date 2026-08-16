import asyncio
import logging
from datetime import datetime, timezone
import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot, Dispatcher

from core.config import settings
from core.runtime import configure_logging
from database.db import init_db
from scheduler.worker import MonitoringWorker
from bot.notifier import TelegramNotifier
from bot.handlers import router as bot_router
from api.server import app as fastapi_app

logger = configure_logging("legacy")

async def main():
    logger.info("Initializing Buyerly AI Media Buyer Service...")
    
    # 1. Инициализация базы данных
    await init_db()

    # 2. Настраиваем FastAPI/Uvicorn веб-сервер
    uvicorn_config = uvicorn.Config(
        app=fastapi_app,
        host=settings.API_HOST,
        port=settings.API_PORT,
        log_level="warning",
        loop="asyncio"
    )
    api_server = uvicorn.Server(uvicorn_config)
    api_task = asyncio.create_task(api_server.serve())
    logger.info(f"🚀 Web API & Mini App running on http://{settings.API_HOST}:{settings.API_PORT}")

    # 3. Проверяем наличие токена бота
    if not settings.BOT_TOKEN:
        logger.warning("⚠️ BOT_TOKEN is empty in .env! Web API is running, bot polling skipped.")
        try:
            await api_task
        except asyncio.CancelledError:
            pass
        return

    # 4. Инициализируем Telegram-бота
    bot = Bot(token=settings.BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(bot_router)

    notifier = TelegramNotifier(bot=bot, target_chat_id=settings.ADMIN_CHAT_ID)
    worker = MonitoringWorker(telegram_notifier=notifier.send_alert)

    # The worker wakes up every minute and only polls accounts/rules that are due.
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        worker.run_cycle,
        "interval",
        minutes=1,
        id="monitoring_job",
        next_run_time=datetime.now(timezone.utc),
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info("Scheduler started: 1-minute dispatch tick with per-rule intervals.")

    # 6. Запускаем polling бота
    logger.info("Starting Telegram Bot polling for Buyerly...")
    try:
        await dp.start_polling(bot)
    finally:
        api_server.should_exit = True
        await api_task
        scheduler.shutdown()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
