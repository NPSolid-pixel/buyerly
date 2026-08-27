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


async def _touch_heartbeat() -> None:
    Path("/tmp/buyerly-worker-heartbeat").touch()


async def _run_day_boundary_tick(worker: MonitoringWorker) -> None:
    """Run one complete account-day scheduling pass and expose readiness."""

    await worker.run_day_boundary_cycle()
    Path("/tmp/buyerly-worker-day-boundary-cycle-complete").touch()


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
    monitoring_worker = MonitoringWorker(
        telegram_notifier=notifier.send_alert
    )
    day_boundary_worker = MonitoringWorker(
        telegram_notifier=notifier.send_alert
    )

    heartbeat_file = Path("/tmp/buyerly-worker-heartbeat")
    day_boundary_cycle_file = Path(
        "/tmp/buyerly-worker-day-boundary-cycle-complete"
    )
    day_boundary_cycle_file.unlink(missing_ok=True)
    heartbeat_file.touch()
    Path("/tmp/buyerly-worker-ready").touch()

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _touch_heartbeat,
        "interval",
        seconds=10,
        id="heartbeat_job",
        next_run_time=datetime.now(timezone.utc),
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        monitoring_worker.run_cycle,
        "interval",
        minutes=1,
        id="monitoring_job",
        next_run_time=datetime.now(timezone.utc),
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _run_day_boundary_tick,
        "interval",
        minutes=1,
        id="account_day_boundary_job",
        next_run_time=datetime.now(timezone.utc),
        max_instances=1,
        coalesce=True,
        args=[day_boundary_worker],
    )
    scheduler.start()
    logger.info(
        "Monitoring and account day-boundary workers started with one-minute dispatch ticks"
    )

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
        heartbeat_file.unlink(missing_ok=True)
        day_boundary_cycle_file.unlink(missing_ok=True)
        Path("/tmp/buyerly-worker-ready").unlink(missing_ok=True)
        await monitoring_worker.meta_client.aclose()
        await day_boundary_worker.meta_client.aclose()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
