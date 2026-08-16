import logging
from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery
from sqlalchemy import select, delete

from database.db import async_session_maker
from database.models import Account, StoppedAdSet, AppSettings
from meta_api.client import MetaClient
from bot.keyboards import (
    get_main_menu_keyboard,
    get_period_keyboard,
    get_interval_keyboard,
    get_account_manage_keyboard
)

logger = logging.getLogger(__name__)
router = Router()
meta_client = MetaClient()

# Глобальная ссылка на планировщик (устанавливается в main.py)
scheduler_ref = None

def set_scheduler(sched):
    global scheduler_ref
    scheduler_ref = sched


@router.message(CommandStart())
async def cmd_start(message: Message):
    from core.config import settings
    if not settings.ADMIN_CHAT_ID:
        settings.ADMIN_CHAT_ID = str(message.chat.id)
        logger.info(f"Auto-configured ADMIN_CHAT_ID: {message.chat.id}")

    text = (
        "👋 <b>Добро пожаловать в Buyerly!</b>\n\n"
        f"Ваш Telegram Chat ID (<code>{message.chat.id}</code>) подключен для получения алертов.\n\n"
        "<b>Возможности системы:</b>\n"
        "• ⏱ <b>Авто-мониторинг:</b> регулярная проверка спенда и конверсий (лиды + реги)\n"
        "• 🛑 <b>Авто-отключение:</b> ступенчатые стопы ($2 при 0 лидов, $6 при 1 лиде)\n"
        "• 🟢 <b>Ловля долета лидов:</b> предложение включить адсет в 1 клик\n"
        "• 🚀 <b>Уведомление о старте (00:00):</b> пуш, когда кабинет начинает крутить рекламу\n"
        "• 📊 <b>Сводки и Расходы:</b> за сегодня, вчера, 3 дня, неделю\n\n"
        "Используйте кнопки меню ниже для управления."
    )
    await message.answer(text, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")


# ----------------------------------------------------
# 1. СВОДКА (ВЫБОР ПЕРИОДА)
# ----------------------------------------------------
@router.message(F.text == "📊 Сводка")
@router.message(Command("summary"))
async def cmd_summary(message: Message):
    text = (
        "📊 <b>Аналитическая сводка:</b>\n\n"
        "Выберите период для просмотра динамики расходов и конверсий:"
    )
    await message.answer(text, reply_markup=get_period_keyboard(), parse_mode="HTML")


@router.callback_query(F.data.startswith("report_period:"))
async def cb_report_period(callback: CallbackQuery):
    period = callback.data.split(":")[1]
    period_names = {
        "today": "Сегодня",
        "yesterday": "Вчера",
        "last_3d": "Последние 3 дня",
        "last_7d": "Неделя (7 дней)"
    }
    period_title = period_names.get(period, period)
    
    await callback.answer(f"Загружаю данные за {period_title}...")
    await callback.message.edit_text(f"⏳ Собираю статистику за <b>{period_title}</b> из Meta API...", parse_mode="HTML")

    async with async_session_maker() as session:
        stmt = select(Account).where(Account.is_active == True)
        res = await session.execute(stmt)
        accounts = res.scalars().all()

        if not accounts:
            await callback.message.edit_text("ℹ️ Нет активных кабинетов для сбора статистики.")
            return

        total_spend = 0.0
        total_leads = 0
        total_regs = 0
        total_active_adsets = 0
        total_paused_adsets = 0
        account_summaries = []

        for acc in accounts:
            try:
                adsets = await meta_client.get_adsets_insights(
                    account_id=acc.account_id,
                    access_token=acc.access_token,
                    date_preset=period
                )
                
                acc_spend = sum(a["spend"] for a in adsets)
                acc_leads = sum(a["leads"] for a in adsets)
                acc_regs = sum(a["registrations"] for a in adsets)
                acc_conversions = acc_leads + acc_regs
                acc_active = sum(1 for a in adsets if a["status"] == "ACTIVE")
                acc_paused = sum(1 for a in adsets if a["status"] == "PAUSED")

                total_spend += acc_spend
                total_leads += acc_leads
                total_regs += acc_regs
                total_active_adsets += acc_active
                total_paused_adsets += acc_paused

                acc_cpa = (acc_spend / acc_conversions) if acc_conversions > 0 else 0.0
                account_summaries.append(
                    f"🏢 <b>{acc.name}</b> (<code>{acc.timezone_name}</code>):\n"
                    f"   💰 Спенд: <b>${acc_spend:.2f}</b> | 🎯 Лиды: {acc_leads} | 📝 Реги: {acc_regs}\n"
                    f"   📈 CPA: <b>${acc_cpa:.2f}</b> | Адсеты: {acc_active} акт. / {acc_paused} пауза"
                )
            except Exception as e:
                logger.error(f"Error fetching report for {acc.account_id}: {e}")
                account_summaries.append(f"⚠️ <b>{acc.name}</b>: Ошибка API ({e})")

        total_all_conversions = total_leads + total_regs
        overall_cpa = (total_spend / total_all_conversions) if total_all_conversions > 0 else 0.0

        report_text = (
            f"📊 <b>Сводный отчет ({period_title}):</b>\n\n"
            f"💵 <b>Общий спенд:</b> ${total_spend:.2f} USD\n"
            f"🎯 <b>Всего лидов:</b> {total_leads}\n"
            f"📝 <b>Всего регистраций:</b> {total_regs}\n"
            f"📈 <b>Средний CPA:</b> ${overall_cpa:.2f}\n"
            f"⚡ <b>Адсеты:</b> {total_active_adsets} активных / {total_paused_adsets} остановлено\n\n"
            f"<b>По кабинетам:</b>\n\n" + "\n\n".join(account_summaries)
        )

        await callback.message.edit_text(report_text, reply_markup=get_period_keyboard(), parse_mode="HTML")


# ----------------------------------------------------
# 2. БЫСТРЫЙ ПОДСЧЕТ РАСХОДОВ (В 1 КЛИК)
# ----------------------------------------------------
@router.message(F.text == "💵 Расходы")
@router.message(Command("spend"))
async def cmd_spend(message: Message):
    wait_msg = await message.answer("⏳ Считаю расходы по всем кабинетам за сегодня...")

    async with async_session_maker() as session:
        stmt = select(Account).where(Account.is_active == True)
        res = await session.execute(stmt)
        accounts = res.scalars().all()

        if not accounts:
            await wait_msg.edit_text("ℹ️ Нет активных кабинетов.")
            return

        total_spend = 0.0
        lines = []

        for acc in accounts:
            try:
                adsets = await meta_client.get_adsets_insights(
                    account_id=acc.account_id,
                    access_token=acc.access_token,
                    date_preset="today"
                )
                acc_spend = sum(a["spend"] for a in adsets)
                total_spend += acc_spend
                lines.append(f"• <b>{acc.name}</b>: ${acc_spend:.2f}")
            except Exception as e:
                lines.append(f"• <b>{acc.name}</b>: <i>ошибка API</i>")

        text = (
            f"💵 <b>Суммарный расход за сегодня:</b>\n"
            f"💰 <b>${total_spend:.2f} USD</b>\n\n"
            f"<b>Разбивка по кабинетам:</b>\n" + "\n".join(lines)
        )
        await wait_msg.edit_text(text, parse_mode="HTML")


# ----------------------------------------------------
# 3. СПИСОК КАБИНЕТОВ И УПРАВЛЕНИЕ ЛИМИТАМИ
# ----------------------------------------------------
@router.message(F.text == "🏢 Кабинеты")
@router.message(Command("accounts"))
async def cmd_accounts(message: Message):
    async with async_session_maker() as session:
        stmt = select(Account)
        res = await session.execute(stmt)
        accounts = res.scalars().all()

        if not accounts:
            await message.answer(
                "ℹ️ У вас пока нет подключенных кабинетов.\n"
                "Чтобы добавить кабинет, используйте команду:\n"
                "<code>/add act_1083480094013618 Швеция_3286 ТОКЕН</code>",
                parse_mode="HTML"
            )
            return

        for acc in accounts:
            status_icon = "🟢 Мониторится" if acc.is_active else "🔴 На паузе"
            text = (
                f"🏢 <b>{acc.name}</b> (<code>{acc.account_id}</code>)\n"
                f"Статус: <b>{status_icon}</b> | Таймзона: <code>{acc.timezone_name}</code>\n\n"
                f"⚙️ <b>Текущие лимиты правил:</b>\n"
                f"• 0 лидов/рег → стоп при <b>${acc.max_spend_0_leads:.2f}</b>\n"
                f"• 1 лид/рега → стоп при <b>${acc.max_spend_1_lead:.2f}</b>\n"
                f"• 2+ лида/реги → макс CPA <b>${acc.max_cpa_multiple_leads:.2f}</b>"
            )
            await message.answer(
                text, 
                reply_markup=get_account_manage_keyboard(acc.account_id, acc.is_active),
                parse_mode="HTML"
            )


@router.callback_query(F.data.startswith("toggle_acc:"))
async def cb_toggle_acc(callback: CallbackQuery):
    account_id = callback.data.split(":")[1]
    async with async_session_maker() as session:
        res = await session.execute(select(Account).where(Account.account_id == account_id))
        acc = res.scalar_one_or_none()
        if acc:
            acc.is_active = not acc.is_active
            await session.commit()
            status_str = "🟢 возобновлен" if acc.is_active else "🔴 поставлен на паузу"
            await callback.answer(f"Мониторинг кабинета {status_str}")
            await callback.message.edit_reply_markup(
                reply_markup=get_account_manage_keyboard(acc.account_id, acc.is_active)
            )


@router.callback_query(F.data.startswith("delete_acc:"))
async def cb_delete_acc(callback: CallbackQuery):
    account_id = callback.data.split(":")[1]
    async with async_session_maker() as session:
        await session.execute(delete(Account).where(Account.account_id == account_id))
        await session.commit()
    await callback.answer("Кабинет удален из базы.")
    await callback.message.edit_text(f"🗑 Кабинет <code>{account_id}</code> удален.", parse_mode="HTML")


@router.callback_query(F.data.startswith("edit_limits:"))
async def cb_edit_limits(callback: CallbackQuery):
    account_id = callback.data.split(":")[1]
    text = (
        f"✏️ <b>Настройка лимитов для <code>{account_id}</code>:</b>\n\n"
        f"Отправьте команду:\n"
        f"<code>/set_limits {account_id} СТОП_0_ЛИДОВ СТОП_1_ЛИД МАКС_CPA</code>\n\n"
        f"<i>Пример:</i>\n"
        f"<code>/set_limits {account_id} 3.0 8.0 8.0</code>"
    )
    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()


@router.message(Command("set_limits"))
async def cmd_set_limits(message: Message):
    parts = message.text.split()
    if len(parts) < 5:
        await message.answer(
            "❌ Неверный формат. Используйте:\n"
            "<code>/set_limits act_ID СТОП_0 СТОП_1 МАКС_CPA</code>\n"
            "<i>Пример:</i> <code>/set_limits act_1083480094013618 3.0 8.0 8.0</code>",
            parse_mode="HTML"
        )
        return

    account_id = parts[1]
    try:
        lim0 = float(parts[2])
        lim1 = float(parts[3])
        lim_multi = float(parts[4])
    except ValueError:
        await message.answer("❌ Лимиты должны быть числами (например: 2.0 6.0 6.0).")
        return

    async with async_session_maker() as session:
        res = await session.execute(select(Account).where(Account.account_id == account_id))
        acc = res.scalar_one_or_none()
        if not acc:
            await message.answer(f"❌ Кабинет <code>{account_id}</code> не найден.", parse_mode="HTML")
            return

        acc.max_spend_0_leads = lim0
        acc.max_spend_1_lead = lim1
        acc.max_cpa_multiple_leads = lim_multi
        await session.commit()

        await message.answer(
            f"✅ <b>Лимиты для {acc.name} успешно обновлены:</b>\n"
            f"• 0 лидов/рег: стоп при <b>${lim0:.2f}</b>\n"
            f"• 1 лид/рега: стоп при <b>${lim1:.2f}</b>\n"
            f"• 2+ лида/реги: макс CPA <b>${lim_multi:.2f}</b>",
            parse_mode="HTML"
        )


# ----------------------------------------------------
# 4. НАСТРОЙКИ (ИНТЕРВАЛ ПРОВЕРКИ 10, 15, 30, 60 МИН)
# ----------------------------------------------------
@router.message(F.text == "⚙️ Настройки")
@router.message(Command("settings"))
async def cmd_settings(message: Message):
    async with async_session_maker() as session:
        res = await session.execute(select(AppSettings).limit(1))
        app_settings = res.scalar_one_or_none()
        interval = app_settings.poll_interval_minutes if app_settings else 15

    text = (
        "⚙️ <b>Настройки системы:</b>\n\n"
        f"⏱ <b>Текущий интервал опроса:</b> <code>{interval} минут</code>\n\n"
        "Выберите частоту автоматической проверки кабинетов:"
    )
    await message.answer(text, reply_markup=get_interval_keyboard(interval), parse_mode="HTML")


@router.callback_query(F.data.startswith("set_interval:"))
async def cb_set_interval(callback: CallbackQuery):
    minutes = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        res = await session.execute(select(AppSettings).limit(1))
        app_settings = res.scalar_one_or_none()
        if not app_settings:
            app_settings = AppSettings(poll_interval_minutes=minutes)
            session.add(app_settings)
        else:
            app_settings.poll_interval_minutes = minutes
        await session.commit()

    # Перенастраиваем таймер планировщика
    global scheduler_ref
    if scheduler_ref:
        scheduler_ref.reschedule_job(
            "monitoring_job",
            trigger="interval",
            minutes=minutes
        )
        logger.info(f"Rescheduled monitoring job to {minutes} minutes.")

    await callback.answer(f"Интервал изменен на {minutes} минут!")
    await callback.message.edit_text(
        f"✅ <b>Интервал опроса успешно обновлен на {minutes} минут!</b>",
        reply_markup=get_interval_keyboard(minutes),
        parse_mode="HTML"
    )


# ----------------------------------------------------
# 5. ДОБАВЛЕНИЕ КАБИНЕТА
# ----------------------------------------------------
@router.message(F.text == "➕ Добавить кабинет")
async def cmd_add_account_info(message: Message):
    text = (
        "➕ <b>Добавление нового кабинета:</b>\n\n"
        "Отправьте команду в формате:\n"
        "<code>/add act_ID НАЗВАНИЕ ТОКЕН [СТОП_0] [СТОП_1] [МАКС_CPA]</code>\n\n"
        "<i>Пример (с базовыми лимитами $2 / $6):</i>\n"
        "<code>/add act_1083480094013618 Швеция_3286 EAAM4nh...</code>\n\n"
        "<i>Пример (со своими лимитами):</i>\n"
        "<code>/add act_1083480094013618 Швеция_3286 EAAM4nh... 3.0 8.0 8.0</code>\n\n"
        "💡 <i>Бот автоматически определит часовой пояс кабинета и поставит его на мониторинг.</i>"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("add"))
async def cmd_add_account(message: Message):
    parts = message.text.split()
    if len(parts) < 4:
        await message.answer(
            "❌ Неверный формат.\n"
            "Используйте: <code>/add act_ID НАЗВАНИЕ ТОКЕН [СТОП_0] [СТОП_1] [МАКС_CPA]</code>",
            parse_mode="HTML"
        )
        return

    account_id = parts[1]
    name = parts[2]
    token = parts[3]
    lim0 = float(parts[4]) if len(parts) > 4 else 2.0
    lim1 = float(parts[5]) if len(parts) > 5 else 6.0
    lim_multi = float(parts[6]) if len(parts) > 6 else 6.0

    # Получаем таймзону из Meta API
    try:
        acc_info = await meta_client.get_account_info(account_id, token)
        timezone_name = acc_info.get("timezone_name", "UTC")
    except Exception as e:
        logger.warning(f"Could not fetch timezone for {account_id}: {e}")
        timezone_name = "UTC"

    async with async_session_maker() as session:
        a_res = await session.execute(select(Account).where(Account.account_id == account_id))
        existing_acc = a_res.scalar_one_or_none()

        if existing_acc:
            existing_acc.name = name
            existing_acc.access_token = token
            existing_acc.timezone_name = timezone_name
            existing_acc.max_spend_0_leads = lim0
            existing_acc.max_spend_1_lead = lim1
            existing_acc.max_cpa_multiple_leads = lim_multi
            existing_acc.is_active = True
            await session.commit()
            await message.answer(
                f"✅ Кабинет <b>{name}</b> (<code>{account_id}</code>) успешно обновлен!\n"
                f"🕒 Таймзона: <code>{timezone_name}</code>\n"
                f"⚙️ Лимиты: ${lim0:.2f} / ${lim1:.2f} / CPA ${lim_multi:.2f}",
                parse_mode="HTML"
            )
        else:
            new_acc = Account(
                account_id=account_id,
                name=name,
                access_token=token,
                timezone_name=timezone_name,
                max_spend_0_leads=lim0,
                max_spend_1_lead=lim1,
                max_cpa_multiple_leads=lim_multi,
                is_active=True
            )
            session.add(new_acc)
            await session.commit()
            await message.answer(
                f"🎉 Кабинет <b>{name}</b> (<code>{account_id}</code>) успешно добавлен на мониторинг!\n"
                f"🕒 Таймзона: <code>{timezone_name}</code>\n"
                f"⚙️ Лимиты: 0 лидов → ${lim0:.2f}, 1 лид → ${lim1:.2f}, 2+ лида → CPA ${lim_multi:.2f}",
                parse_mode="HTML"
            )


# ----------------------------------------------------
# 5.1 ИНСТРУКЦИЯ ПО ПОЛУЧЕНИЮ ТОКЕНА
# ----------------------------------------------------
@router.message(F.text == "🔑 Инструкция по токену")
@router.message(Command("token_help"))
async def cmd_token_help(message: Message):
    text = (
        "🔑 <b>Инструкция: Как получить Access Token в Meta Business Manager</b>\n\n"
        "<b>1️⃣ Шаг 1: Создать системного юзера в БМ</b>\n"
        "• Перейдите в <i>Business Settings → Users → System Users</i>\n"
        "• Нажмите <b>Add</b>, задайте имя (например <code>Buyerly Bot</code>) и выберите роль <b>Admin</b>.\n\n"
        "<b>2️⃣ Шаг 2: Добавить владельца App в этот же БМ</b>\n"
        "• Перейдите в <i>Users → People</i> и пригласите аккаунт, на котором создано приложение (Владельца App).\n\n"
        "<b>3️⃣ Шаг 3: Добавить App в БМ и расшарить доступ к РК</b>\n"
        "• В <i>Accounts → Apps</i> добавьте приложение.\n"
        "• В <i>Accounts → Ad Accounts</i> выберите нужные рекламные кабинеты и выдайте полный доступ (<b>Full Control</b>).\n\n"
        "<b>4️⃣ Шаг 4: Выдать доступ к App системному юзеру и создать токен</b>\n"
        "• В <i>Accounts → Apps</i> выберите приложение → <i>Assign System Users</i> → добавьте созданного юзера с правами <b>Full Control</b>.\n"
        "• Вернитесь в <i>Users → System Users</i> → нажмите <b>Generate New Token</b>:\n"
        "   — Выберите приложение;\n"
        "   — Срок действия: <b>Never</b> (или 60 days);\n"
        "   — Отметьте 2 галочки: <code>ads_management</code> и <code>ads_read</code>.\n\n"
        "📋 <i>Скопируйте полученный токен (начинается на EAAM...) и добавьте кабинет командой:</i>\n"
        "<code>/add act_ID НАЗВАНИЕ ТОКЕН</code>"
    )
    await message.answer(text, parse_mode="HTML")


# ----------------------------------------------------
# 6. РЕАКТИВАЦИЯ И ОТКЛОНЕНИЕ (ИНЛАЙН КНОПКИ)
# ----------------------------------------------------
@router.callback_query(F.data.startswith("reactivate:"))
async def cb_reactivate(callback: CallbackQuery):
    _, account_id, adset_id = callback.data.split(":")

    async with async_session_maker() as session:
        acc_res = await session.execute(select(Account).where(Account.account_id == account_id))
        account = acc_res.scalar_one_or_none()

        if not account:
            await callback.answer("❌ Кабинет не найден в базе данных.", show_alert=True)
            return

        try:
            await meta_client.set_adset_status(
                adset_id=adset_id,
                access_token=account.access_token,
                status="ACTIVE"
            )

            stopped_res = await session.execute(
                select(StoppedAdSet).where(StoppedAdSet.adset_id == adset_id)
            )
            stopped_entry = stopped_res.scalar_one_or_none()
            if stopped_entry:
                stopped_entry.is_resolved = True
                await session.commit()

            await callback.answer("✅ Адсет успешно включен!")
            new_text = callback.message.text + "\n\n🟢 <b>СТАТУС: Включен пользователем через Telegram ✅</b>"
            await callback.message.edit_text(new_text, reply_markup=None, parse_mode="HTML")

        except Exception as e:
            logger.error(f"Error reactivating adset {adset_id}: {e}")
            await callback.answer(f"❌ Ошибка Meta API: {e}", show_alert=True)


@router.callback_query(F.data.startswith("dismiss:"))
async def cb_dismiss(callback: CallbackQuery):
    _, account_id, adset_id = callback.data.split(":")

    async with async_session_maker() as session:
        stopped_res = await session.execute(
            select(StoppedAdSet).where(StoppedAdSet.adset_id == adset_id)
        )
        stopped_entry = stopped_res.scalar_one_or_none()
        if stopped_entry:
            stopped_entry.is_resolved = True
            await session.commit()

    await callback.answer("Оставлен выключенным.")
    new_text = callback.message.text + "\n\n⚪ <b>СТАТУС: Оставлен выключенным пользователем ❌</b>"
    await callback.message.edit_text(new_text, reply_markup=None, parse_mode="HTML")
