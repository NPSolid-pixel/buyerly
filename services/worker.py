import asyncio
import signal
from datetime import datetime, timezone
from pathlib import Path

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.notifier import TelegramNotifier
from core.config import settings
from core.runtime import configure_logging
from scheduler.worker import MonitoringWorker


async def main() -> None:
    logger = configure_logging("worker")
    if not settings.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is required for worker notifications")
    bot = Bot(token=settings.BOT_TOKEN)
    await bot.get_me()
    notifier = TelegramNotifier(
        bot=bot,
        target_chat_id=settings.ADMIN_CHAT_ID,
    )
    worker = MonitoringWorker(
        telegram_notifier=notifier.send_alert
    )

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
    Path("/tmp/buyerly-worker-ready").touch()
    logger.info("Monitoring worker started with a one-minute dispatch tick")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop_event.set)
        except NotImplementedError:
            pass

    try:
        await stop_event.wait()
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
