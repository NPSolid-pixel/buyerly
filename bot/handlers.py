import logging
import re
from typing import List
from aiogram import Router, F, Bot
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from sqlalchemy import select, delete

from core.config import settings
from database.db import async_session_maker
from database.models import Account, StoppedAdSet, AppSettings, TelegramUser
from meta_api.client import MetaClient
from bot.keyboards import (
    get_main_menu_keyboard,
    get_cancel_keyboard,
    get_period_keyboard,
    get_interval_keyboard,
    get_account_manage_keyboard,
    get_admin_approval_keyboard
)

logger = logging.getLogger(__name__)
router = Router()
meta_client = MetaClient()

# Глобальная ссылка на планировщик (устанавливается в main.py)
scheduler_ref = None

def set_scheduler(sched):
    global scheduler_ref
    scheduler_ref = sched


# ----------------------------------------------------
# FSM: ПОШАГОВЫЙ МАСТЕР ДОБАВЛЕНИЯ ПАЧКИ КАБИНЕТОВ
# ----------------------------------------------------
class BatchAccountAddStates(StatesGroup):
    waiting_for_ids = State()
    waiting_for_name = State()
    waiting_for_token = State()


# ----------------------------------------------------
# ПРОВЕРКА АВТОРИЗАЦИИ И ДОСТУПА ПОЛЬЗОВАТЕЛЯ
# ----------------------------------------------------
async def check_user_access(message: Message, bot: Bot) -> bool:
    tg_id = str(message.from_user.id)
    username = message.from_user.username or ""
    full_name = message.from_user.full_name or ""

    # Авто-назначение главного админа из .env
    if not settings.ADMIN_CHAT_ID:
        settings.ADMIN_CHAT_ID = tg_id

    is_super_admin = (tg_id == str(settings.ADMIN_CHAT_ID))

    async with async_session_maker() as session:
        res = await session.execute(select(TelegramUser).where(TelegramUser.telegram_id == tg_id))
        user = res.scalar_one_or_none()

        if not user:
            user = TelegramUser(
                telegram_id=tg_id,
                username=username,
                full_name=full_name,
                role="admin" if is_super_admin else "buyer",
                is_approved=True if is_super_admin else False
            )
            session.add(user)
            await session.commit()

            # Если новый пользователь (не админ) — шлем уведомление главному админу
            if not is_super_admin and settings.ADMIN_CHAT_ID:
                try:
                    admin_text = (
                        f"🔔 <b>Новый запрос на доступ в Buyerly!</b>\n\n"
                        f"👤 <b>Пользователь:</b> {full_name} (@{username})\n"
                        f"🆔 <b>Telegram ID:</b> <code>{tg_id}</code>\n\n"
                        f"Предоставить доступ к системе?"
                    )
                    await bot.send_message(
                        chat_id=settings.ADMIN_CHAT_ID,
                        text=admin_text,
                        reply_markup=get_admin_approval_keyboard(tg_id),
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.error(f"Error notifying admin about new user {tg_id}: {e}")

        if not user.is_approved:
            await message.answer(
                f"⛔️ <b>Доступ ограничен</b>\n\n"
                f"Ваш Telegram ID: <code>{tg_id}</code>\n"
                f"Запрос на доступ отправлен администратору. Как только доступ будет одобрен, вам придет уведомление.",
                parse_mode="HTML"
            )
            return False

        return True


# ----------------------------------------------------
# 1. КОМАНДА /START И ГЛАВНОЕ МЕНЮ
# ----------------------------------------------------
@router.message(StateFilter("*"), CommandStart())
async def cmd_start(message: Message, bot: Bot, state: FSMContext):
    await state.clear()
    has_access = await check_user_access(message, bot)
    if not has_access:
        return

    tg_id = str(message.from_user.id)
    is_admin = (tg_id == str(settings.ADMIN_CHAT_ID))

    text = (
        "👋 <b>Добро пожаловать в Buyerly!</b>\n\n"
        "Автономная система мониторинга и авто-правил Facebook Ads.\n\n"
        "<b>Возможности:</b>\n"
        "• ⏱ <b>Авто-мониторинг:</b> проверка спенда, лидов и регистраций каждые 10–60 мин\n"
        "• 🛑 <b>Ступенчатые стопы:</b> $2 при 0 лидов/рег, $6 при 1 лиде/реге, CPA $6 при 2+\n"
        "• 🟢 <b>Ловля долета:</b> кнопка мгновенного включения адсета при долете конверсии\n"
        "• 🚀 <b>Пуш о старте (00:00):</b> уведомление о начале открута в часовом поясе кабинета\n"
        "• 📊 <b>Сводки и Расходы:</b> персональная статистика по вашим кабинетам\n\n"
        "Используйте кнопки меню ниже для управления."
    )
    await message.answer(text, reply_markup=get_main_menu_keyboard(is_admin=is_admin), parse_mode="HTML")


# ----------------------------------------------------
# 2. ПОШАГОВОЕ ДОБАВЛЕНИЕ КАБИНЕТОВ (ПАЧКОЙ)
# ----------------------------------------------------
@router.message(StateFilter("*"), F.text.in_(["➕ Добавить кабинеты", "➕ Добавить кабинет", "/add"]))
async def start_add_wizard(message: Message, bot: Bot, state: FSMContext):
    await state.clear()
    has_access = await check_user_access(message, bot)
    if not has_access:
        return

    await state.set_state(BatchAccountAddStates.waiting_for_ids)
    text = (
        "➕ <b>Добавление рекламных кабинетов (Шаг 1 из 3)</b>\n\n"
        "Отправьте ID рекламных кабинетов (каждый с новой строки или через пробел).\n"
        "<i>Можно отправить один или сразу пачку из 5–20 кабинетов!</i>\n\n"
        "<b>Пример:</b>\n"
        "<code>act_1083480094013618\n"
        "1070862758952340\n"
        "1387608033301866</code>"
    )
    await message.answer(text, reply_markup=get_cancel_keyboard(), parse_mode="HTML")


@router.message(StateFilter("*"), F.text.in_(["❌ Отменить добавление", "❌ Отмена", "/cancel"]))
async def cancel_add_wizard(message: Message, state: FSMContext):
    await state.clear()
    tg_id = str(message.from_user.id)
    is_admin = (tg_id == str(settings.ADMIN_CHAT_ID))
    await message.answer("❌ Добавление кабинетов отменено.", reply_markup=get_main_menu_keyboard(is_admin=is_admin))


@router.message(BatchAccountAddStates.waiting_for_ids)
async def process_account_ids(message: Message, state: FSMContext):
    raw_text = message.text.strip()
    
    # Если нажал отмену
    if raw_text in ["❌ Отменить добавление", "❌ Отмена", "/cancel"]:
        await state.clear()
        tg_id = str(message.from_user.id)
        is_admin = (tg_id == str(settings.ADMIN_CHAT_ID))
        await message.answer("❌ Добавление кабинетов отменено.", reply_markup=get_main_menu_keyboard(is_admin=is_admin))
        return

    # Извлекаем все ID
    found_ids = re.findall(r"(?:act_)?(\d{6,25})", raw_text)
    
    if not found_ids:
        await message.answer("❌ Не удалось найти ID кабинетов. Пожалуйста, отправьте числовые ID (например: <code>1083480094013618</code>).", parse_mode="HTML")
        return

    clean_ids = [f"act_{i}" for i in list(dict.fromkeys(found_ids))]
    await state.update_data(account_ids=clean_ids)
    await state.set_state(BatchAccountAddStates.waiting_for_name)

    text = (
        f"✅ <b>Распознано кабинетов: {len(clean_ids)} шт.</b>\n\n"
        "📝 <b>Шаг 2 из 3: Название для пачки</b>\n\n"
        "Введите общее название для этих кабинетов (например: <code>Швеция</code> или <code>Underdog</code>).\n"
        "<i>Бот автоматически пронумерует их: Швеция 1, Швеция 2, Швеция 3...</i>\n\n"
        "💡 <i>Или отправьте знак <code>-</code> (дефис), чтобы сохранить оригинальные названия из Facebook.</i>"
    )
    await message.answer(text, reply_markup=get_cancel_keyboard(), parse_mode="HTML")


@router.message(BatchAccountAddStates.waiting_for_name)
async def process_batch_name(message: Message, state: FSMContext):
    batch_name = message.text.strip()
    await state.update_data(batch_name=batch_name)
    await state.set_state(BatchAccountAddStates.waiting_for_token)

    text = (
        "🔑 <b>Шаг 3 из 3: Access Token</b>\n\n"
        "Отправьте токен доступа системного пользователя (System User Token):\n"
        "<i>(Один токен подходит сразу ко всей пачке кабинетов)</i>\n\n"
        "💡 <i>Если у вас нет токена, нажмите '🔑 Инструкция по токену'.</i>"
    )
    await message.answer(text, reply_markup=get_cancel_keyboard(), parse_mode="HTML")


@router.message(BatchAccountAddStates.waiting_for_token)
async def process_token_and_save(message: Message, state: FSMContext):
    token = message.text.strip()
    data = await state.get_data()
    account_ids: List[str] = data.get("account_ids", [])
    batch_name = data.get("batch_name", "-")
    owner_id = str(message.from_user.id)

    progress_msg = await message.answer(f"⏳ Проверяю и подключаю {len(account_ids)} кабинетов через Meta API...")

    added_results = []
    error_results = []

    async with async_session_maker() as session:
        for idx, acc_id in enumerate(account_ids, start=1):
            try:
                acc_info = await meta_client.get_account_info(acc_id, token)
                timezone_name = acc_info.get("timezone_name", "UTC")
                fb_name = acc_info.get("name", acc_id)
                
                # Формируем имя с авто-нумерацией если задано общее имя
                if batch_name != "-" and len(batch_name) > 0:
                    display_name = f"{batch_name} {idx}" if len(account_ids) > 1 else batch_name
                else:
                    display_name = fb_name

                # Проверяем наличие в БД
                res = await session.execute(select(Account).where(Account.account_id == acc_id))
                existing = res.scalar_one_or_none()

                if existing:
                    existing.name = display_name
                    existing.access_token = token
                    existing.timezone_name = timezone_name
                    existing.owner_id = owner_id
                    existing.batch_name = batch_name if batch_name != "-" else ""
                    existing.is_active = True
                else:
                    new_acc = Account(
                        account_id=acc_id,
                        name=display_name,
                        access_token=token,
                        owner_id=owner_id,
                        batch_name=batch_name if batch_name != "-" else "",
                        timezone_name=timezone_name,
                        max_spend_0_leads=2.0,
                        max_spend_1_lead=6.0,
                        max_cpa_multiple_leads=6.0,
                        is_active=True
                    )
                    session.add(new_acc)

                added_results.append(f"• <b>{display_name}</b> (<code>{acc_id}</code>) — {timezone_name}")

            except Exception as e:
                logger.error(f"Error adding account {acc_id}: {e}")
                error_results.append(f"• <code>{acc_id}</code>: {e}")

        await session.commit()

    await state.clear()
    is_admin = (owner_id == str(settings.ADMIN_CHAT_ID))

    result_text = f"🎉 <b>Успешно подключено: {len(added_results)} из {len(account_ids)} кабинетов!</b>\n\n"
    if added_results:
        result_text += "<b>Подключенные кабинеты:</b>\n" + "\n".join(added_results) + "\n\n"
    if error_results:
        result_text += "⚠️ <b>Ошибки подключения:</b>\n" + "\n".join(error_results) + "\n\n"

    result_text += (
        "⚙️ <b>Базовые лимиты:</b> 0 лидов → $2.00, 1 лид → $6.00, 2+ → CPA $6.00\n"
        "<i>Кабинеты поставлены на автоматический фоновый мониторинг.</i>"
    )

    await progress_msg.edit_text(result_text, parse_mode="HTML")
    await message.answer("Главное меню:", reply_markup=get_main_menu_keyboard(is_admin=is_admin))


# ----------------------------------------------------
# 3. СВОДКА (ПЕРСОНАЛЬНАЯ ПО БАЙЕРУ)
# ----------------------------------------------------
@router.message(StateFilter("*"), F.text.in_(["📊 Сводка", "/summary"]))
async def cmd_summary(message: Message, bot: Bot, state: FSMContext):
    await state.clear()
    has_access = await check_user_access(message, bot)
    if not has_access:
        return

    text = (
        "📊 <b>Аналитическая сводка по вашим кабинетам:</b>\n\n"
        "Выберите период для просмотра динамики расходов и конверсий:"
    )
    await message.answer(text, reply_markup=get_period_keyboard(), parse_mode="HTML")


@router.callback_query(F.data.startswith("report_period:"))
async def cb_report_period(callback: CallbackQuery):
    period = callback.data.split(":")[1]
    user_id = str(callback.from_user.id)
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
        # Фильтруем строго по владельцу кабинетов
        stmt = select(Account).where(Account.is_active == True, Account.owner_id == user_id)
        res = await session.execute(stmt)
        accounts = res.scalars().all()

        if not accounts:
            await callback.message.edit_text(
                "ℹ️ У вас пока нет активных подключенных кабинетов.\n"
                "Нажмите '➕ Добавить кабинеты' в меню.",
                reply_markup=None
            )
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
            f"📊 <b>Ваш сводный отчет ({period_title}):</b>\n\n"
            f"💵 <b>Общий спенд:</b> ${total_spend:.2f} USD\n"
            f"🎯 <b>Всего лидов:</b> {total_leads}\n"
            f"📝 <b>Всего регистраций:</b> {total_regs}\n"
            f"📈 <b>Средний CPA:</b> ${overall_cpa:.2f}\n"
            f"⚡ <b>Адсеты:</b> {total_active_adsets} активных / {total_paused_adsets} остановлено\n\n"
            f"<b>По вашим кабинетам:</b>\n\n" + "\n\n".join(account_summaries)
        )

        await callback.message.edit_text(report_text, reply_markup=get_period_keyboard(), parse_mode="HTML")


# ----------------------------------------------------
# 4. РАСХОДЫ (В 1 КЛИК ПО БАЙЕРУ)
# ----------------------------------------------------
@router.message(StateFilter("*"), F.text.in_(["💵 Расходы", "/spend"]))
async def cmd_spend(message: Message, bot: Bot, state: FSMContext):
    await state.clear()
    has_access = await check_user_access(message, bot)
    if not has_access:
        return

    user_id = str(message.from_user.id)
    wait_msg = await message.answer("⏳ Считаю расходы по вашим кабинетам за сегодня...")

    async with async_session_maker() as session:
        stmt = select(Account).where(Account.is_active == True, Account.owner_id == user_id)
        res = await session.execute(stmt)
        accounts = res.scalars().all()

        if not accounts:
            await wait_msg.edit_text("ℹ️ У вас пока нет активных кабинетов.")
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
            f"💵 <b>Ваш суммарный расход за сегодня:</b>\n"
            f"💰 <b>${total_spend:.2f} USD</b>\n\n"
            f"<b>Разбивка по кабинетам:</b>\n" + "\n".join(lines)
        )
        await wait_msg.edit_text(text, parse_mode="HTML")


# ----------------------------------------------------
# 5. СПИСОК КАБИНЕТОВ ПОЛЬЗОВАТЕЛЯ
# ----------------------------------------------------
@router.message(StateFilter("*"), F.text.in_(["🏢 Мои кабинеты", "🏢 Кабинеты", "/accounts"]))
async def cmd_accounts(message: Message, bot: Bot, state: FSMContext):
    await state.clear()
    has_access = await check_user_access(message, bot)
    if not has_access:
        return

    user_id = str(message.from_user.id)

    async with async_session_maker() as session:
        stmt = select(Account).where(Account.owner_id == user_id)
        res = await session.execute(stmt)
        accounts = res.scalars().all()

        if not accounts:
            await message.answer(
                "ℹ️ У вас пока нет подключенных кабинетов.\n"
                "Чтобы добавить кабинеты, нажмите кнопку <b>➕ Добавить кабинеты</b> в меню.",
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


@router.message(StateFilter("*"), Command("set_limits"))
async def cmd_set_limits(message: Message, bot: Bot, state: FSMContext):
    await state.clear()
    has_access = await check_user_access(message, bot)
    if not has_access:
        return

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

    user_id = str(message.from_user.id)
    async with async_session_maker() as session:
        res = await session.execute(
            select(Account).where(Account.account_id == account_id, Account.owner_id == user_id)
        )
        acc = res.scalar_one_or_none()
        if not acc:
            await message.answer(f"❌ Кабинет <code>{account_id}</code> не найден среди ваших кабинетов.", parse_mode="HTML")
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
# 6. НАСТРОЙКИ ЧАСТОТЫ ОПРОСА
# ----------------------------------------------------
@router.message(StateFilter("*"), F.text.in_(["⚙️ Настройки", "/settings"]))
async def cmd_settings(message: Message, bot: Bot, state: FSMContext):
    await state.clear()
    has_access = await check_user_access(message, bot)
    if not has_access:
        return

    async with async_session_maker() as session:
        res = await session.execute(select(AppSettings).limit(1))
        app_settings = res.scalar_one_or_none()
        interval = app_settings.poll_interval_minutes if app_settings else 10

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
# 7. ИНСТРУКЦИЯ ПО ТОКЕНУ
# ----------------------------------------------------
@router.message(StateFilter("*"), F.text.in_(["🔑 Инструкция по токену", "🔑 Инструкция к токену", "/token_help"]))
async def cmd_token_help(message: Message, bot: Bot, state: FSMContext):
    await state.clear()
    has_access = await check_user_access(message, bot)
    if not has_access:
        return

    text = (
        "🔑 <b>Инструкция: Как получить Access Token в Meta Business Manager</b>\n\n"
        "<b>1️⃣ Шаг 1: Создать системного юзера в БМ</b>\n"
        "• В <i>Business Settings → Users → System Users</i> нажмите <b>Add</b> (роль <b>Admin</b>).\n\n"
        "<b>2️⃣ Шаг 2: Добавить владельца App в этот же БМ</b>\n"
        "• В <i>Users → People</i> пригласите аккаунт Facebook, на котором создано приложение.\n\n"
        "<b>3️⃣ Шаг 3: Добавить App в БМ и расшарить доступ к РК</b>\n"
        "• В <i>Accounts → Apps</i> добавьте приложение.\n"
        "• В <i>Accounts → Ad Accounts</i> выберите нужные рекламные кабинеты и выдайте полный доступ (<b>Full Control</b>).\n\n"
        "<b>4️⃣ Шаг 4: Выдать доступ к App системному юзеру и создать токен</b>\n"
        "• В <i>Accounts → Apps</i> выберите приложение → <i>Assign System Users</i> → добавьте созданного юзера с правами <b>Full Control</b>.\n"
        "• Вернитесь в <i>Users → System Users</i> → нажмите <b>Generate New Token</b>:\n"
        "   — Выберите приложение;\n"
        "   — Срок действия: <b>Never</b> (или 60 days);\n"
        "   — Отметьте 2 галочки: <code>ads_management</code> и <code>ads_read</code>.\n\n"
        "📋 <i>Скопируйте токен и нажмите кнопку '➕ Добавить кабинеты' в меню!</i>"
    )
    await message.answer(text, parse_mode="HTML")


# ----------------------------------------------------
# 8. АДМИН-ПАНЕЛЬ И ОДОБРЕНИЕ ДОСТУПА
# ----------------------------------------------------
@router.message(StateFilter("*"), F.text.in_(["👑 Админ-панель", "/admin"]))
async def cmd_admin_panel(message: Message, bot: Bot, state: FSMContext):
    await state.clear()
    tg_id = str(message.from_user.id)
    if tg_id != str(settings.ADMIN_CHAT_ID):
        await message.answer("⛔️ Эта панель доступна только главному администратору.")
        return

    async with async_session_maker() as session:
        # Список пользователей
        u_res = await session.execute(select(TelegramUser))
        users = u_res.scalars().all()

        # Список всех кабинетов команды
        a_res = await session.execute(select(Account))
        all_accounts = a_res.scalars().all()

        active_count = sum(1 for a in all_accounts if a.is_active)
        approved_users = [u for u in users if u.is_approved]

        user_lines = []
        for u in approved_users:
            u_accs = sum(1 for a in all_accounts if a.owner_id == u.telegram_id)
            user_lines.append(f"• <b>{u.full_name}</b> (@{u.username}) | Кабинетов: {u_accs}")

        text = (
            "👑 <b>Админ-панель управления Buyerly:</b>\n\n"
            f"👥 <b>Пользователей в команде:</b> {len(approved_users)}\n"
            f"🏢 <b>Всего кабинетов на мониторинге:</b> {len(all_accounts)} (Активных: {active_count})\n\n"
            f"<b>Состав команды:</b>\n" + ("\n".join(user_lines) if user_lines else "<i>Пока только вы</i>")
        )
        await message.answer(text, parse_mode="HTML")


@router.message(StateFilter("*"), Command("events"))
async def cmd_view_events(message: Message, bot: Bot, state: FSMContext):
    await state.clear()
    tg_id = str(message.from_user.id)
    if tg_id != str(settings.ADMIN_CHAT_ID):
        return

    from database.models import EventLog
    async with async_session_maker() as session:
        stmt = select(EventLog).order_by(EventLog.created_at.desc()).limit(15)
        res = await session.execute(stmt)
        logs = res.scalars().all()

        if not logs:
            await message.answer("ℹ️ Журнал событий пока пуст.")
            return

        lines = []
        for l in logs:
            status_icon = "✅" if l.status == "SUCCESS" else "❌"
            time_str = l.created_at.strftime("%H:%M:%S")
            lines.append(f"{status_icon} <code>{time_str}</code> [{l.event_type}] → <code>{l.target_chat_id}</code>")

        await message.answer("📜 <b>Последние 15 событий доставки алертов:</b>\n\n" + "\n".join(lines), parse_mode="HTML")


@router.callback_query(F.data.startswith("approve_user:"))
async def cb_approve_user(callback: CallbackQuery, bot: Bot):
    target_tg_id = callback.data.split(":")[1]

    async with async_session_maker() as session:
        res = await session.execute(select(TelegramUser).where(TelegramUser.telegram_id == target_tg_id))
        user = res.scalar_one_or_none()

        if user:
            user.is_approved = True
            await session.commit()

            # Уведомляем пользователя
            try:
                await bot.send_message(
                    chat_id=target_tg_id,
                    text="🎉 <b>Администратор одобрил ваш доступ в Buyerly!</b>\n\n"
                         "Нажмите /start для начала работы.",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Failed to notify approved user {target_tg_id}: {e}")

            await callback.message.edit_text(
                f"✅ <b>Доступ пользователю {user.full_name} (@{user.username}) одобрен!</b>",
                parse_mode="HTML"
            )
            await callback.answer("Пользователь одобрен!")


@router.callback_query(F.data.startswith("reject_user:"))
async def cb_reject_user(callback: CallbackQuery):
    target_tg_id = callback.data.split(":")[1]

    async with async_session_maker() as session:
        await session.execute(delete(TelegramUser).where(TelegramUser.telegram_id == target_tg_id))
        await session.commit()

    await callback.message.edit_text(f"❌ <b>Запрос на доступ (ID: <code>{target_tg_id}</code>) отклонен.</b>", parse_mode="HTML")
    await callback.answer("Запрос отклонен.")


# ----------------------------------------------------
# 9. РЕАКТИВАЦИЯ И ОТКЛОНЕНИЕ (ИНЛАЙН КНОПКИ)
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
