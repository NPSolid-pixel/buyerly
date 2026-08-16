import logging
from typing import Optional
from aiogram import Bot
from rules.engine import RuleEvaluationResult
from bot.keyboards import get_reactivate_keyboard
from database.db import async_session_maker
from database.models import EventLog

logger = logging.getLogger(__name__)

class TelegramNotifier:
    """
    Форматирует и отправляет алерты и отчеты в Telegram конкретному владельцу или админу
    с обязательной фиксацией каждого события в логах и базе данных EventLog.
    """

    def __init__(self, bot: Bot, target_chat_id: str = ""):
        self.bot = bot
        self.default_chat_id = target_chat_id

    async def _save_event_log(self, event_type: str, chat_id: str, account_id: str, message: str, status: str = "SUCCESS"):
        try:
            async with async_session_maker() as session:
                log_entry = EventLog(
                    event_type=event_type,
                    target_chat_id=str(chat_id),
                    account_id=str(account_id),
                    message=message,
                    status=status
                )
                session.add(log_entry)
                await session.commit()
        except Exception as e:
            logger.error(f"Failed to save EventLog to DB: {e}")

    async def send_alert(
        self,
        event_type: str,
        account_name: str,
        account_id: str,
        target_chat_id: str = "",
        eval_result: Optional[RuleEvaluationResult] = None,
        timezone_name: str = "",
        local_time: str = "",
        active_count: int = 0,
        start_spend: float = 0.0
    ):
        from core.config import settings
        chat_id = target_chat_id or self.default_chat_id or settings.ADMIN_CHAT_ID
        if not chat_id:
            logger.warning(f"No target_chat_id configured for event {event_type} (Account: {account_id}). Alert skipped.")
            return

        text = ""
        keyboard = None

        try:
            # 1. ОСТАНОВКА АДСЕТА
            if event_type == "STOP" and eval_result:
                text = (
                    f"🛑 <b>Авто-отключение AdSet</b>\n\n"
                    f"🏢 <b>Кабинет:</b> {account_name} (<code>{account_id}</code>)\n"
                    f"🎯 <b>AdSet:</b> <code>{eval_result.adset_name}</code> (ID: <code>{eval_result.adset_id}</code>)\n"
                    f"💰 <b>Спенд:</b> ${eval_result.spend:.2f}\n"
                    f"👥 <b>Лидов:</b> {eval_result.leads} | <b>Рег:</b> {eval_result.registrations}\n"
                    f"📊 <b>CPA:</b> ${eval_result.cpa:.2f}\n\n"
                    f"⚠️ <i>Причина: {eval_result.reason}</i>"
                )

            # 2. ДОЛЕТ ЛИДА / РЕГИ (ПРЕДЛОЖЕНИЕ ВКЛЮЧИТЬ)
            elif event_type == "PROPOSE_REACTIVATE" and eval_result:
                text = (
                    f"🟢 <b>Долетел результат в остановленный AdSet!</b>\n\n"
                    f"🏢 <b>Кабинет:</b> {account_name} (<code>{account_id}</code>)\n"
                    f"🎯 <b>AdSet:</b> <code>{eval_result.adset_name}</code> (ID: <code>{eval_result.adset_id}</code>)\n"
                    f"💰 <b>Итоговый спенд:</b> ${eval_result.spend:.2f}\n"
                    f"👥 <b>Лидов:</b> {eval_result.leads} | <b>Рег:</b> {eval_result.registrations}\n"
                    f"🎯 <b>Итоговый CPA:</b> ${eval_result.cpa:.2f}\n\n"
                    f"❓ <i>Результат вошел в допустимую норму. Включить адсет обратно?</i>"
                )
                keyboard = get_reactivate_keyboard(
                    account_id=account_id,
                    adset_id=eval_result.adset_id
                )

            # 3. АВТО-ВКЛЮЧЕНИЕ
            elif event_type == "AUTO_REACTIVATE" and eval_result:
                text = (
                    f"⚡ <b>Авто-возобновление AdSet (Долетел результат)</b>\n\n"
                    f"🏢 <b>Кабинет:</b> {account_name} (<code>{account_id}</code>)\n"
                    f"🎯 <b>AdSet:</b> <code>{eval_result.adset_name}</code>\n"
                    f"💰 <b>Спенд:</b> ${eval_result.spend:.2f} | <b>Лиды:</b> {eval_result.leads} | <b>Реги:</b> {eval_result.registrations} | <b>CPA:</b> ${eval_result.cpa:.2f}\n"
                    f"✅ <i>Адсет автоматически переведен в статус ACTIVE.</i>"
                )

            # 3.1. ТОЛЬКО УВЕДОМЛЕНИЕ (Send notification only)
            elif event_type == "NOTIFY_ONLY" and eval_result:
                text = (
                    f"🔔 <b>Внимание: Сработало правило (Только пуш)</b>\n\n"
                    f"🏢 <b>Кабинет:</b> {account_name} (<code>{account_id}</code>)\n"
                    f"🎯 <b>AdSet:</b> <code>{eval_result.adset_name}</code> (ID: <code>{eval_result.adset_id}</code>)\n"
                    f"💰 <b>Спенд:</b> ${eval_result.spend:.2f}\n"
                    f"👥 <b>Лидов:</b> {eval_result.leads} | <b>Рег:</b> {eval_result.registrations}\n"
                    f"📊 <b>CPA:</b> ${eval_result.cpa:.2f}\n\n"
                    f"⚠️ <i>{eval_result.reason}</i>"
                )
                from bot.keyboards import get_pause_adset_keyboard
                keyboard = get_pause_adset_keyboard(
                    account_id=account_id,
                    adset_id=eval_result.adset_id
                )

            # 4. СТАРТ ОТКРУТА В 00:00 ПО ВРЕМЕНИ КАБИНЕТА
            elif event_type == "DAY_START":
                text = (
                    f"🚀 <b>Кабинет начал открут рекламы!</b>\n\n"
                    f"🏢 <b>Кабинет:</b> {account_name} (<code>{account_id}</code>)\n"
                    f"🕒 <b>Время кабинета:</b> <code>{local_time}</code>\n"
                    f"⚡ <b>Активных адсетов:</b> {active_count}\n"
                    f"💰 <b>Стартовый спенд:</b> ${start_spend:.2f}\n\n"
                    f"<i>Бот непрерывно мониторит кампании.</i>"
                )

            # 5. ПРОБЛЕМА С КАБИНЕТОМ (БАН / ХОЛД / ПРОВЕРКА)
            elif event_type == "ACCOUNT_ISSUE":
                text = (
                    f"🚨 <b>ВНИМАНИЕ: Проблема со статусом кабинета в Meta!</b>\n\n"
                    f"🏢 <b>Кабинет:</b> {account_name} (<code>{account_id}</code>)\n"
                    f"⚠️ <b>Статус:</b> {local_time}\n\n"
                    f"🛑 <i>Мониторинг этого кабинета временно приостановлен во избежание ошибок.</i>"
                )

            # 6. СЛЕТЕВШИЙ ТОКЕН ДОСТУПА
            elif event_type == "TOKEN_EXPIRED":
                text = (
                    f"🔑 <b>ВНИМАНИЕ: Слетел Access Token Meta API!</b>\n\n"
                    f"🏢 <b>Кабинет:</b> {account_name} (<code>{account_id}</code>)\n"
                    f"⚠️ <i>Токен доступа стал недействительным или истек срок действия.</i>\n\n"
                    f"💡 <i>Обновите токен через бота (кнопка '➕ Добавить кабинеты').</i>"
                )

            if text:
                logger.info(f"Sending Telegram alert [{event_type}] to chat_id={chat_id} (Account: {account_id})")
                await self.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
                logger.info(f"✅ Alert [{event_type}] delivered successfully to chat_id={chat_id}")
                await self._save_event_log(event_type=event_type, chat_id=chat_id, account_id=account_id, message=text, status="SUCCESS")

        except Exception as e:
            logger.error(f"❌ Failed to send telegram alert [{event_type}] to {chat_id}: {e}")
            await self._save_event_log(event_type=event_type, chat_id=chat_id, account_id=account_id, message=f"FAILED: {e}\n{text}", status="ERROR")
