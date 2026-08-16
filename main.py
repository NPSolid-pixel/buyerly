import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot, Dispatcher
from sqlalchemy import select

from core.config import settings
from database.db import init_db, async_session_maker
from database.models import AppSettings
from scheduler.worker import MonitoringWorker
from bot.notifier import TelegramNotifier
from bot.handlers import router as bot_router, set_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("ai_mediabuyer")

async def main():
    logger.info("Initializing AI Media Buyer Service...")
    
    # 1. Инициализация базы данных
    await init_db()

    # Получаем сохраненный интервал из БД или по умолчанию 15 мин
    async with async_session_maker() as session:
        res = await session.execute(select(AppSettings).limit(1))
        app_settings = res.scalar_one_or_none()
        interval = app_settings.poll_interval_minutes if app_settings else settings.DEFAULT_POLL_INTERVAL_MINUTES
        if not app_settings:
            session.add(AppSettings(poll_interval_minutes=interval))
            await session.commit()

    # 2. Проверяем наличие токена бота
    if not settings.BOT_TOKEN:
        logger.warning("⚠️ BOT_TOKEN is empty in .env! Running worker once.")
        worker = MonitoringWorker()
        stats = await worker.run_cycle()
        logger.info(f"Initial monitoring cycle finished: {stats}")
        return

    # 3. Инициализируем Telegram-бота
    bot = Bot(token=settings.BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(bot_router)

    notifier = TelegramNotifier(bot=bot, target_chat_id=settings.ADMIN_CHAT_ID)
    worker = MonitoringWorker(telegram_notifier=notifier.send_alert)

    # 4. Настраиваем планировщик периодического мониторинга
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        worker.run_cycle,
        "interval",
        minutes=interval,
        id="monitoring_job"
    )
    scheduler.start()
    set_scheduler(scheduler)
    logger.info(f"Scheduler started: polling accounts every {interval} minutes.")

    # 5. Запускаем polling бота
    logger.info("Starting Telegram Bot polling...")
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
