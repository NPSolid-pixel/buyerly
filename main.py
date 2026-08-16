import os
import asyncio
import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot, Dispatcher
from sqlalchemy import select

from core.config import settings
from database.db import init_db, async_session_maker
from database.models import AppSettings
from scheduler.worker import MonitoringWorker
from bot.notifier import TelegramNotifier
from bot.handlers import router as bot_router, set_scheduler
from api.server import app as fastapi_app

# Настройка сквозного логирования (в консоль + в файл logs/buyerly.log)
os.makedirs("logs", exist_ok=True)
log_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

file_handler = RotatingFileHandler("logs/buyerly.log", maxBytes=10*1024*1024, backupCount=5, encoding="utf-8")
file_handler.setFormatter(log_formatter)
file_handler.setLevel(logging.INFO)

console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
console_handler.setLevel(logging.INFO)

logging.basicConfig(
    level=logging.INFO,
    handlers=[file_handler, console_handler]
)
logger = logging.getLogger("ai_mediabuyer")

async def main():
    logger.info("Initializing Buyerly AI Media Buyer Service...")
    
    # 1. Инициализация базы данных
    await init_db()

    # Получаем сохраненный интервал из БД или по умолчанию 10 мин
    async with async_session_maker() as session:
        res = await session.execute(select(AppSettings).limit(1))
        app_settings = res.scalar_one_or_none()
        interval = app_settings.poll_interval_minutes if app_settings else 10
        if not app_settings:
            session.add(AppSettings(poll_interval_minutes=interval))
            await session.commit()

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

    # 5. Настраиваем планировщик периодического мониторинга
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        worker.run_cycle,
        "interval",
        minutes=interval,
        id="monitoring_job",
        next_run_time=datetime.now(timezone.utc)
    )
    scheduler.start()
    set_scheduler(scheduler)
    logger.info(f"Scheduler started: polling accounts every {interval} minutes.")

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
