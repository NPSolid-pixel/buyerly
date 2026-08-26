import json
import logging
import re
import time
import uuid
from typing import List
from aiogram import Router, F, Bot
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from sqlalchemy import and_, delete, or_, select

from core.config import settings
from core.audit import build_audit_event
from core.action_undo import UndoError, undo_audit_action
from core.currency import format_money, normalize_currency
from core.ownership import entity_is_owned_by, owned_by
from core.meta_tokens import resolve_account_access_token
from core.timezones import resolve_account_clock
from database.db import async_session_maker
from database.models import Account, AppSettings, StoppedAdSet, User, WorkspaceMember
from meta_api.client import MetaClient
from bot.keyboards import (
    get_main_menu_keyboard,
    get_cancel_keyboard,
    get_period_keyboard,
    get_interval_keyboard,
    get_account_manage_keyboard,
    get_admin_approval_keyboard,
    get_undo_action_keyboard,
)

logger = logging.getLogger(__name__)
router = Router()
meta_client = MetaClient()

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

    is_configured_admin = bool(settings.ADMIN_CHAT_ID) and (
        tg_id == str(settings.ADMIN_CHAT_ID)
    )

    async with async_session_maker() as session:
        res = await session.execute(select(User).where(User.telegram_id == tg_id))
        user = res.scalar_one_or_none()

        if not user:
            user = User(
                telegram_id=tg_id,
                username=username or f"user_{tg_id}",
                full_name=full_name,
                role="admin" if is_configured_admin else "buyer",
                is_approved=is_configured_admin,
            )
            session.add(user)
            await session.commit()

            # Notify every approved database admin about a pending request.
            if not is_configured_admin:
                admin_result = await session.execute(
                    select(User.telegram_id).where(
                        User.role == "admin",
                        User.is_approved == True,
                        User.telegram_id.is_not(None),
                    )
                )
                admin_ids = list(dict.fromkeys(admin_result.scalars().all()))
                for admin_id in admin_ids:
                    admin_text = (
                        f"🔔 <b>Новый запрос на доступ в Buyerly!</b>\n\n"
                        f"👤 <b>Пользователь:</b> {full_name} (@{username})\n"
                        f"🆔 <b>Telegram ID:</b> <code>{tg_id}</code>\n\n"
                        f"Предоставить доступ к системе?"
                    )
                    try:
                        await bot.send_message(
                            chat_id=admin_id,
                            text=admin_text,
                            reply_markup=get_admin_approval_keyboard(tg_id),
                            parse_mode="HTML",
                        )
                    except Exception as e:
                        logger.error(
                            "Error notifying an admin about new user %s: %s",
                            tg_id,
                            e,
                        )

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
    async with async_session_maker() as session:
        is_admin = await _is_admin_user(session, tg_id)

    text = (
        "👋 <b>Добро пожаловать в Buyerly!</b>\n\n"
        "Автономная система мониторинга и авто-правил Facebook Ads.\n\n"
        "<b>Возможности:</b>\n"
        "• ⏱ <b>Авто-мониторинг:</b> проверка спенда, лидов и регистраций каждые 10–60 мин\n"
        "• 🛑 <b>Авто-правила:</b> гибкий конструктор условий (CPL, CPReg, CPP, Spend и события) с AND/OR логикой\n"
        "• 🟢 <b>Ловля долета:</b> кнопка мгновенного включения адсета при долете конверсии\n"
        "• 🌅 <b>Новые сутки кабинета:</b> точное уведомление в 00:00 по часовому поясу Meta\n"
        "• 📊 <b>Сводки и Расходы:</b> персональная статистика по вашим кабинетам\n\n"
        "Используйте кнопки меню ниже для управления."
    )
    await message.answer(text, reply_markup=get_main_menu_keyboard(is_admin=is_admin), parse_mode="HTML")


def parse_fb_raw_accounts(raw_text: str) -> List[dict]:
    """
    Умный парсер: извлекает ID кабинетов и их имена даже из сырого текста Facebook Business Manager.
    Безопасен к чрезмерно длинным входным данным (DoS / ReDoS mitigation).
    """
    if not raw_text:
        return []
    if len(raw_text) > 65536:
        raw_text = raw_text[:65536]

    lines = [l.strip() for l in raw_text.strip().split("\n") if l.strip()]
    if len(lines) > 2000:
        lines = lines[:2000]

    id_name_pairs = []
    
    for i, line in enumerate(lines):
        match = re.search(r"(?:Ad account ID|Account ID|ID|act_)[:\s]*(\d{8,25})", line, re.IGNORECASE)
        if match:
            acc_id = f"act_{match.group(1)}"
            name = ""
            if i > 0 and not re.search(r"(?:Ad account ID|Owned by|info for|scope|permission)", lines[i-1], re.IGNORECASE):
                name = lines[i-1][:120].strip()
            id_name_pairs.append((acc_id, name))
            
    if not id_name_pairs:
        all_ids = re.findall(r"(?:act_)?(\d{8,25})", raw_text)
        for num in list(dict.fromkeys(all_ids)):
            id_name_pairs.append((f"act_{num}", ""))
            
    seen = set()
    final_list = []
    for acc_id, name in id_name_pairs:
        if acc_id not in seen:
            seen.add(acc_id)
            final_list.append({"account_id": acc_id, "parsed_name": name})
        if len(final_list) >= 500:
            break
    return final_list


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
        "Отправьте список ID кабинетов или <b>скопируйте текст прямо из Facebook Business Manager</b>!\n\n"
        "💡 <i>Бот сам автоматически распознает все ID кабинетов и их названия из текста.</i>\n\n"
        "<b>Пример:</b>\n"
        "<code>act_1083480094013618\n"
        "1070862758952340</code>\n"
        "<i>или вставьте скопированный блок с 'Ad account ID: ...'</i>"
    )
    await message.answer(text, reply_markup=get_cancel_keyboard(), parse_mode="HTML")


@router.message(StateFilter("*"), F.text.in_(["❌ Отменить добавление", "❌ Отмена", "/cancel"]))
async def cancel_add_wizard(message: Message, state: FSMContext):
    await state.clear()
    tg_id = str(message.from_user.id)
    async with async_session_maker() as session:
        is_admin = await _is_admin_user(session, tg_id)
    await message.answer("❌ Добавление кабинетов отменено.", reply_markup=get_main_menu_keyboard(is_admin=is_admin))


@router.message(BatchAccountAddStates.waiting_for_ids)
async def process_account_ids(message: Message, state: FSMContext):
    raw_text = message.text.strip()
    
    # Если нажал отмену
    if raw_text in ["❌ Отменить добавление", "❌ Отмена", "/cancel"]:
        await state.clear()
        tg_id = str(message.from_user.id)
        async with async_session_maker() as session:
            is_admin = await _is_admin_user(session, tg_id)
        await message.answer("❌ Добавление кабинетов отменено.", reply_markup=get_main_menu_keyboard(is_admin=is_admin))
        return

    parsed_accounts = parse_fb_raw_accounts(raw_text)
    
    if not parsed_accounts:
        await message.answer("❌ Не удалось найти ID кабинетов. Пожалуйста, отправьте числовые ID или скопируйте текст из Facebook.", parse_mode="HTML")
        return

    await state.update_data(parsed_accounts=parsed_accounts)
    await state.set_state(BatchAccountAddStates.waiting_for_name)

    lines = []
    for a in parsed_accounts[:10]:
        name_str = f" ({a['parsed_name']})" if a['parsed_name'] else ""
        lines.append(f"• <code>{a['account_id']}</code>{name_str}")
    if len(parsed_accounts) > 10:
        lines.append(f"<i>...и еще {len(parsed_accounts) - 10} кабинетов</i>")

    text = (
        f"✅ <b>Распознано кабинетов: {len(parsed_accounts)} шт.</b>\n"
        + "\n".join(lines) + "\n\n"
        "📝 <b>Шаг 2 из 3: Название для пачки</b>\n\n"
        "Введите общее название (например: <code>Швеция</code> или <code>Underdog</code>).\n"
        "<i>Бот автоматически пронумерует их: Швеция 1, Швеция 2...</i>\n\n"
        "💡 <i>Или отправьте <code>-</code> (дефис), чтобы сохранить найденные названия из Facebook.</i>"
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
    parsed_accounts: List[dict] = data.get("parsed_accounts", [])
    batch_name = data.get("batch_name", "-")
    owner_id = str(message.from_user.id)

    progress_msg = await message.answer(f"⏳ Проверяю и подключаю {len(parsed_accounts)} кабинетов через Meta API...")

    added_results = []
    error_results = []

    async with async_session_maker() as session:
        owner_user = (
            await session.execute(
                select(User).where(User.telegram_id == owner_id)
            )
        ).scalar_one_or_none()
        if not owner_user or not owner_user.is_approved:
            await state.clear()
            await progress_msg.edit_text("⛔️ Доступ не подтверждён.")
            return
        for idx, item in enumerate(parsed_accounts, start=1):
            acc_id = item["account_id"]
            parsed_name = item.get("parsed_name", "")

            try:
                acc_info = await meta_client.get_account_info(acc_id, token)
                timezone_name = str(acc_info.get("timezone_name") or "").strip()
                if resolve_account_clock(timezone_name) is None:
                    raise RuntimeError(
                        "Meta не вернула поддерживаемый часовой пояс рекламного кабинета."
                    )
                fb_name = acc_info.get("name", acc_id)
                currency = normalize_currency(acc_info.get("currency"))
                
                # Формируем имя
                if batch_name != "-" and len(batch_name) > 0:
                    display_name = f"{batch_name} {idx}" if len(parsed_accounts) > 1 else batch_name
                elif parsed_name:
                    display_name = parsed_name
                else:
                    display_name = fb_name

                # Проверяем наличие в БД
                res = await session.execute(select(Account).where(Account.account_id == acc_id))
                existing = res.scalar_one_or_none()

                if existing:
                    if (
                        existing.owner_user_id is not None
                        and existing.owner_user_id != owner_user.id
                        and not is_admin
                    ):
                        error_results.append(
                            f"• <code>{acc_id}</code>: Кабинет уже привязан к другому пользователю."
                        )
                        continue

                    if existing.timezone_name != timezone_name:
                        existing.last_day_start_date = ""
                    existing.name = display_name
                    existing.access_token = token
                    existing.timezone_name = timezone_name
                    existing.currency = currency
                    existing.owner_user_id = owner_user.id
                    existing.batch_name = batch_name if batch_name != "-" else ""
                    existing.is_active = True
                else:
                    new_acc = Account(
                        workspace_id=owner_user.active_workspace_id,
                        account_id=acc_id,
                        name=display_name,
                        access_token=token,
                        owner_user_id=owner_user.id,
                        batch_name=batch_name if batch_name != "-" else "",
                        timezone_name=timezone_name,
                        currency=currency,
                        rules_enabled=False,
                        is_active=True
                    )
                    session.add(new_acc)

                added_results.append(f"• <b>{display_name}</b> (<code>{acc_id}</code>) — {timezone_name} · {currency}")

            except Exception as e:
                logger.error(f"Error adding account {acc_id}: {e}")
                error_results.append(f"• <code>{acc_id}</code>: {e}")

        await session.commit()
        is_admin = await _is_admin_user(session, owner_id)

    await state.clear()

    result_text = f"🎉 <b>Успешно подключено: {len(added_results)} из {len(parsed_accounts)} кабинетов!</b>\n\n"
    if added_results:
        result_text += "<b>Подключенные кабинеты:</b>\n" + "\n".join(added_results) + "\n\n"
    if error_results:
        result_text += "⚠️ <b>Ошибки подключения:</b>\n" + "\n".join(error_results) + "\n\n"

    result_text += (
        "📊 <b>Режим:</b> 👁 <i>Только аналитика и статистика (Авто-правила выключены).</i>\n"
        "💡 <i>Включить боевые авто-правила стопов можно в любой момент в '🏢 Мои кабинеты'.</i>"
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


def get_short_account_label(name: str, account_id: str) -> str:
    parts = name.strip().split()
    if parts:
        last_part = parts[-1]
        if last_part.isdigit():
            if len(parts) > 1 and len(parts[-2]) <= 8 and not parts[-2].startswith("PrivateCore"):
                return f"{parts[-2]} {last_part}"
            return last_part
    if len(name) <= 8:
        return name
    clean_id = account_id.replace("act_", "")
    return clean_id[-5:]


async def get_user_accounts(session, user_id: str) -> List[Account]:
    user = (
        await session.execute(
            select(User).where(User.telegram_id == user_id)
        )
    ).scalar_one_or_none()
    if not user or not user.is_approved:
        return []
    if user.active_workspace_id:
        stmt = select(Account).where(
            or_(
                Account.workspace_id == user.active_workspace_id,
                and_(Account.workspace_id.is_(None), owned_by(Account, user)),
            )
        )
    else:
        stmt = select(Account).where(owned_by(Account, user))
    res = await session.execute(stmt)
    return res.scalars().all()


async def _is_admin_user(session, user_id: str) -> bool:
    result = await session.execute(
        select(User).where(User.telegram_id == user_id)
    )
    user = result.scalar_one_or_none()
    return bool(user and user.is_approved and user.role == "admin")


async def _can_manage_account(session, user_id: str, account: Account) -> bool:
    result = await session.execute(
        select(User).where(User.telegram_id == user_id)
    )
    user = result.scalar_one_or_none()
    if not user or not user.is_approved:
        return False
    if account.workspace_id is not None:
        member = (
            await session.execute(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == account.workspace_id,
                    WorkspaceMember.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if member and member.role in ("owner", "admin", "buyer"):
            return True
    return entity_is_owned_by(account, user)


@router.callback_query(F.data.startswith("report_period:"))
async def cb_report_period(callback: CallbackQuery):
    period = callback.data.split(":")[1]
    user_id = str(callback.from_user.id)
    period_names = {
        "today": "сегодня",
        "yesterday": "вчера",
        "last_3d": "последние 3 дня",
        "last_7d": "неделю (7 дней)"
    }
    period_title = period_names.get(period, period)
    
    await callback.answer(f"Загружаю данные за {period_title}...")
    await callback.message.edit_text(f"⏳ Собираю статистику за <b>{period_title}</b> из Meta API...", parse_mode="HTML")

    async with async_session_maker() as session:
        accounts = await get_user_accounts(session, user_id)

        if not accounts:
            await callback.message.edit_text(
                "ℹ️ У вас пока нет подключенных кабинетов.\n"
                "Нажмите '➕ Добавить кабинеты' в меню.",
                reply_markup=None
            )
            return

        spend_by_currency = {}
        total_clicks = 0
        total_leads = 0
        total_regs = 0
        total_purchases = 0
        tz_name = "UTC"

        table_rows = []
        purchases_list = []
        no_spend_list = []

        for acc in accounts:
            tz_name = acc.timezone_name or tz_name
            short_name = get_short_account_label(acc.name, acc.account_id)
            try:
                access_token = await resolve_account_access_token(session, acc)
                account_metrics = await meta_client.get_account_insights_summary(
                    account_id=acc.account_id,
                    access_token=access_token,
                    date_preset=period
                )
                currency = normalize_currency(acc.currency)
                acc_spend = account_metrics.get("spend", 0.0)
                acc_clicks = account_metrics.get("clicks", 0)
                acc_leads = account_metrics.get("leads", 0)
                acc_regs = account_metrics.get("registrations", 0)
                acc_purchases = account_metrics.get("purchases", 0)

                spend_by_currency[currency] = spend_by_currency.get(currency, 0.0) + acc_spend
                total_clicks += acc_clicks
                total_leads += acc_leads
                total_regs += acc_regs
                total_purchases += acc_purchases

                table_rows.append({
                    "name": short_name,
                    "spend": acc_spend,
                    "clicks": acc_clicks,
                    "leads": acc_leads,
                    "regs": acc_regs,
                    "purchases": acc_purchases,
                    "currency": currency,
                })

            except Exception as e:
                logger.error(f"Error fetching report for {acc.account_id}: {e}")
                table_rows.append({
                    "name": short_name,
                    "spend": 0.0,
                    "clicks": 0,
                    "leads": 0,
                    "regs": 0,
                    "purchases": 0,
                    "currency": normalize_currency(acc.currency),
                })

        # Формируем аккуратную таблицу в блоке <pre>
        header = f"{'Кабинет':<10}{'Спенд':>9}{'Клики':>6}{'Лиды':>5}{'Реги':>5}{'Пок':>4}"
        lines = [header]
        for r in table_rows:
            spend_str = format_money(r["spend"], r["currency"])
            lines.append(f"{r['name']:<10}{spend_str:>9}{r['clicks']:>6}{r['leads']:>5}{r['regs']:>5}{r['purchases']:>4}")

        table_block = "<pre>\n" + "\n".join(lines) + "\n</pre>"

        totals_text = " · ".join(
            format_money(amount, currency)
            for currency, amount in sorted(spend_by_currency.items())
        ) or "—"
        report_text = (
            f"📊 <b>Отчёт за {period_title}</b>\n\n"
            f"💵 <code>{totals_text}</code>\n\n"
            f"👆 <b>{total_clicks}</b>  ·  🎯 <b>{total_leads}</b>  ·  📝 <b>{total_regs}</b>  ·  💳 <b>{total_purchases}</b>\n\n"
            f"{table_block}"
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
        accounts = await get_user_accounts(session, user_id)

        if not accounts:
            await wait_msg.edit_text("ℹ️ У вас пока нет подключенных кабинетов.")
            return

        spend_by_currency = {}
        lines = []

        for acc in accounts:
            try:
                access_token = await resolve_account_access_token(session, acc)
                account_metrics = await meta_client.get_account_insights_summary(
                    account_id=acc.account_id,
                    access_token=access_token,
                    date_preset="today"
                )
                currency = normalize_currency(acc.currency)
                acc_spend = account_metrics.get("spend", 0.0)
                spend_by_currency[currency] = spend_by_currency.get(currency, 0.0) + acc_spend
                lines.append(f"• {acc.name}: {format_money(acc_spend, currency)}")
            except Exception as e:
                lines.append(f"• {acc.name}: 🔴 Ошибка получения данных")

        totals_text = " · ".join(
            format_money(amount, currency)
            for currency, amount in sorted(spend_by_currency.items())
        ) or "—"

        text = (
            "<b>Разбивка по кабинетам:</b>\n"
            + "\n".join(lines) + "\n\n"
            f"💵 <b>Расход за сегодня по валютам:</b> <code>{totals_text}</code>"
        )
        await wait_msg.edit_text(text, parse_mode="HTML")


def format_account_card(acc: Account) -> str:
    if acc.account_status == 2 or not acc.is_active:
        status_line = "🔴 <b>Заблокирован в Meta</b>"
    elif acc.account_status == 3:
        status_line = "💳 <b>Проблема с оплатой (Hold)</b>"
    else:
        status_line = "🟢 <b>Активен</b>"

    rules_line = "🛡 <b>Авто-правила: ВКЛЮЧЕНЫ</b>\n" if acc.rules_enabled else ""
    preset_line = f"📋 Правило: <b>{acc.preset_name}</b>\n" if getattr(acc, 'preset_name', None) else ""

    return (
        f"🏢 <b>{acc.name}</b> (<code>{acc.account_id}</code>)\n"
        f"{status_line}\n"
        f"{rules_line}"
        f"{preset_line}"
        f"🕒 Таймзона: <code>{acc.timezone_name}</code>\n"
        f"💱 Валюта: <code>{normalize_currency(acc.currency)}</code>"
    )


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
        accounts = await get_user_accounts(session, user_id)

        if not accounts:
            await message.answer(
                "ℹ️ У вас пока нет подключенных кабинетов.\n"
                "Чтобы добавить кабинеты, нажмите кнопку <b>➕ Добавить кабинеты</b> в меню.",
                parse_mode="HTML"
            )
            return

        for acc in accounts:
            text = format_account_card(acc)
            await message.answer(
                text, 
                reply_markup=get_account_manage_keyboard(acc.account_id, acc.rules_enabled),
                parse_mode="HTML"
            )


@router.callback_query(F.data.startswith("toggle_rules:"))
async def cb_toggle_rules(callback: CallbackQuery):
    account_id = callback.data.split(":")[1]
    user_id = str(callback.from_user.id)
    async with async_session_maker() as session:
        res = await session.execute(select(Account).where(Account.account_id == account_id))
        acc = res.scalar_one_or_none()
        if not acc or not await _can_manage_account(session, user_id, acc):
            await callback.answer("⛔️ Нет доступа к этому кабинету.", show_alert=True)
            return

        try:
            active_rules = json.loads(acc.active_rules or "[]")
        except (TypeError, ValueError):
            active_rules = []
        if not acc.rules_enabled and not active_rules:
            await callback.answer("Сначала привяжите правило в веб-интерфейсе.", show_alert=True)
            return

        acc.rules_enabled = not acc.rules_enabled
        await session.commit()
        status_str = "🟢 ВКЛЮЧЕНЫ" if acc.rules_enabled else "🔴 ВЫКЛЮЧЕНЫ (Только статистика)"
        await callback.answer(f"Авто-правила {status_str}!")
        await callback.message.edit_text(
            format_account_card(acc),
            reply_markup=get_account_manage_keyboard(acc.account_id, acc.rules_enabled),
            parse_mode="HTML"
        )


@router.callback_query(F.data.startswith("delete_acc:"))
async def cb_delete_acc(callback: CallbackQuery):
    account_id = callback.data.split(":")[1]
    user_id = str(callback.from_user.id)
    async with async_session_maker() as session:
        result = await session.execute(select(Account).where(Account.account_id == account_id))
        account = result.scalar_one_or_none()
        if not account or not await _can_manage_account(session, user_id, account):
            await callback.answer("⛔️ Нет доступа к этому кабинету.", show_alert=True)
            return
        await session.execute(delete(Account).where(Account.account_id == account_id))
        await session.commit()
    await callback.answer("Кабинет удален из базы.")
    await callback.message.edit_text(f"🗑 Кабинет <code>{account_id}</code> удален из системы.", parse_mode="HTML")





@router.callback_query(F.data.startswith("back_to_acc:"))
async def cb_back_to_acc(callback: CallbackQuery):
    account_id = callback.data.split(":")[1]
    user_id = str(callback.from_user.id)
    async with async_session_maker() as session:
        res = await session.execute(select(Account).where(Account.account_id == account_id))
        acc = res.scalar_one_or_none()
        if not acc or not await _can_manage_account(session, user_id, acc):
            await callback.answer("⛔️ Нет доступа к этому кабинету.", show_alert=True)
            return
        await callback.message.edit_text(
            format_account_card(acc),
            reply_markup=get_account_manage_keyboard(acc.account_id, acc.rules_enabled),
            parse_mode="HTML"
        )
    await callback.answer()




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
        f"⏱ <b>Базовый интервал мониторинга:</b> <code>{interval} минут</code>\n\n"
        "У авто-правил используется собственный интервал. Выберите частоту проверки статуса остальных кабинетов:"
    )
    await message.answer(text, reply_markup=get_interval_keyboard(interval), parse_mode="HTML")


@router.callback_query(F.data.startswith("set_interval:"))
async def cb_set_interval(callback: CallbackQuery):
    minutes = int(callback.data.split(":")[1])
    user_id = str(callback.from_user.id)

    async with async_session_maker() as session:
        if not await _is_admin_user(session, user_id):
            await callback.answer("⛔️ Только администратор может менять этот интервал.", show_alert=True)
            return
        res = await session.execute(select(AppSettings).limit(1))
        app_settings = res.scalar_one_or_none()
        if not app_settings:
            app_settings = AppSettings(poll_interval_minutes=minutes)
            session.add(app_settings)
        else:
            app_settings.poll_interval_minutes = minutes
        await session.commit()

    await callback.answer(f"Базовый интервал изменен на {minutes} минут!")
    await callback.message.edit_text(
        f"✅ <b>Базовый интервал мониторинга обновлен на {minutes} минут!</b>",
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

    async with async_session_maker() as session:
        if not await _is_admin_user(session, tg_id):
            await message.answer("⛔️ Эта панель доступна только администратору.")
            return

        # Список пользователей
        u_res = await session.execute(select(User))
        users = u_res.scalars().all()

        # Список всех кабинетов команды
        a_res = await session.execute(select(Account))
        all_accounts = a_res.scalars().all()

        active_count = sum(1 for a in all_accounts if a.is_active)
        approved_users = [u for u in users if u.is_approved]

        user_lines = []
        for u in approved_users:
            u_accs = sum(1 for a in all_accounts if entity_is_owned_by(a, u))
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

    from database.models import EventLog
    async with async_session_maker() as session:
        if not await _is_admin_user(session, tg_id):
            return

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
    actor_id = str(callback.from_user.id)

    async with async_session_maker() as session:
        if not await _is_admin_user(session, actor_id):
            await callback.answer("⛔️ Недостаточно прав.", show_alert=True)
            return
        res = await session.execute(select(User).where(User.telegram_id == target_tg_id))
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
    actor_id = str(callback.from_user.id)

    async with async_session_maker() as session:
        if not await _is_admin_user(session, actor_id):
            await callback.answer("⛔️ Недостаточно прав.", show_alert=True)
            return
        await session.execute(delete(User).where(User.telegram_id == target_tg_id))
        await session.commit()

    await callback.message.edit_text(f"❌ <b>Запрос на доступ (ID: <code>{target_tg_id}</code>) отклонен.</b>", parse_mode="HTML")
    await callback.answer("Запрос отклонен.")


# ----------------------------------------------------
# 9. РЕАКТИВАЦИЯ И ОТКЛОНЕНИЕ (ИНЛАЙН КНОПКИ)
# ----------------------------------------------------
@router.callback_query(F.data.startswith("reactivate:"))
async def cb_reactivate(callback: CallbackQuery):
    _, account_id, adset_id = callback.data.split(":")
    user_id = str(callback.from_user.id)
    action_started = time.perf_counter()
    correlation_id = uuid.uuid4().hex
    audit_event_id = None

    async with async_session_maker() as session:
        acc_res = await session.execute(select(Account).where(Account.account_id == account_id))
        account = acc_res.scalar_one_or_none()

        if not account or not await _can_manage_account(session, user_id, account):
            await callback.answer("⛔️ Нет доступа к этому кабинету.", show_alert=True)
            return

        stopped_res = await session.execute(
            select(StoppedAdSet).where(StoppedAdSet.adset_id == adset_id)
        )
        stopped_entry = stopped_res.scalar_one_or_none()
        if not stopped_entry or stopped_entry.account_id != account_id:
            await callback.answer("❌ Запись об остановке не найдена.", show_alert=True)
            return

        try:
            access_token = await resolve_account_access_token(session, account)
            await meta_client.set_adset_status(
                adset_id=adset_id,
                access_token=access_token,
                status="ACTIVE"
            )
        except Exception as e:
            logger.error(f"Error reactivating adset {adset_id}: {e}")
            session.add(
                build_audit_event(
                    account=account,
                    event_type="MANUAL_REACTIVATE",
                    status="ERROR",
                    correlation_id=correlation_id,
                    category="MANUAL_ACTION",
                    action="REACTIVATE_ADSET",
                    message=str(e),
                    before_state={"status": "PAUSED", "is_resolved": False},
                    after_state={"status": "PAUSED", "is_resolved": False},
                    duration_ms=(time.perf_counter() - action_started) * 1000,
                    actor_type="telegram_user",
                    actor_id=user_id,
                    adset_id=adset_id,
                    adset_name=stopped_entry.adset_name,
                )
            )
            try:
                await session.commit()
            except Exception as audit_error:
                await session.rollback()
                logger.error("Failed to persist Telegram reactivation error: %s", audit_error)
            await callback.answer("❌ Meta не смогла включить ad set. Подробности сохранены в логах.", show_alert=True)
            return

        stopped_entry.is_resolved = True
        audit_event = build_audit_event(
                account=account,
                event_type="MANUAL_REACTIVATE",
                status="SUCCESS",
                correlation_id=correlation_id,
                category="MANUAL_ACTION",
                action="REACTIVATE_ADSET",
                message="Ad set включён пользователем через Telegram.",
                before_state={"status": "PAUSED", "is_resolved": False},
                after_state={"status": "ACTIVE", "is_resolved": True},
                duration_ms=(time.perf_counter() - action_started) * 1000,
                actor_type="telegram_user",
                actor_id=user_id,
                adset_id=adset_id,
                adset_name=stopped_entry.adset_name,
            )
        session.add(audit_event)
        try:
            await session.flush()
            audit_event_id = audit_event.id
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.error("Meta activated adset %s but Telegram audit commit failed: %s", adset_id, e)
            await callback.answer("⚠️ Meta включила ad set, но Buyerly не сохранил локальный статус.", show_alert=True)
            return

    await callback.answer("✅ Ad set успешно включён!")
    new_text = callback.message.text + "\n\n🟢 <b>СТАТУС: Включён пользователем через Telegram ✅</b>"
    await callback.message.edit_text(
        new_text,
        reply_markup=get_undo_action_keyboard(audit_event_id) if audit_event_id else None,
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("dismiss:"))
async def cb_dismiss(callback: CallbackQuery):
    _, account_id, adset_id = callback.data.split(":")
    user_id = str(callback.from_user.id)

    async with async_session_maker() as session:
        stopped_res = await session.execute(
            select(StoppedAdSet).where(StoppedAdSet.adset_id == adset_id)
        )
        stopped_entry = stopped_res.scalar_one_or_none()
        account_res = await session.execute(
            select(Account).where(Account.account_id == account_id)
        )
        account = account_res.scalar_one_or_none()
        if (
            not stopped_entry
            or stopped_entry.account_id != account_id
            or not account
            or not await _can_manage_account(session, user_id, account)
        ):
            await callback.answer("⛔️ Нет доступа к этой записи.", show_alert=True)
            return
        stopped_entry.is_resolved = True
        session.add(
            build_audit_event(
                account=account,
                event_type="HIDE_STOPPED_NOTIFICATION",
                status="SUCCESS",
                correlation_id=uuid.uuid4().hex,
                category="MANUAL_ACTION",
                action="HIDE_NOTIFICATION",
                message="Карточка выполненной остановки скрыта через Telegram.",
                before_state={"status": "PAUSED", "is_resolved": False},
                after_state={"status": "PAUSED", "is_resolved": True},
                actor_type="telegram_user",
                actor_id=user_id,
                adset_id=adset_id,
                adset_name=stopped_entry.adset_name,
            )
        )
        await session.commit()

    await callback.answer("Карточка скрыта.")
    new_text = callback.message.text + "\n\n⚪ <b>Карточка скрыта. Ad set остался выключенным.</b>"
    await callback.message.edit_text(new_text, reply_markup=None, parse_mode="HTML")


@router.callback_query(F.data.startswith("pause_adset:"))
async def cb_pause_adset(callback: CallbackQuery):
    _, account_id, adset_id = callback.data.split(":")
    user_id = str(callback.from_user.id)
    action_started = time.perf_counter()
    correlation_id = uuid.uuid4().hex
    audit_event_id = None

    async with async_session_maker() as session:
        acc_res = await session.execute(select(Account).where(Account.account_id == account_id))
        account = acc_res.scalar_one_or_none()

        if not account or not await _can_manage_account(session, user_id, account):
            await callback.answer("⛔️ Нет доступа к этому кабинету.", show_alert=True)
            return

        try:
            access_token = await resolve_account_access_token(session, account)
            await meta_client.set_adset_status(
                adset_id=adset_id,
                access_token=access_token,
                status="PAUSED"
            )
        except Exception as e:
            logger.error(f"Error pausing adset {adset_id}: {e}")
            session.add(
                build_audit_event(
                    account=account,
                    event_type="MANUAL_PAUSE",
                    status="ERROR",
                    correlation_id=correlation_id,
                    category="MANUAL_ACTION",
                    action="PAUSE_ADSET",
                    message=str(e),
                    before_state={"status": "ACTIVE"},
                    after_state={"status": "ACTIVE"},
                    duration_ms=(time.perf_counter() - action_started) * 1000,
                    actor_type="telegram_user",
                    actor_id=user_id,
                    adset_id=adset_id,
                )
            )
            try:
                await session.commit()
            except Exception as audit_error:
                await session.rollback()
                logger.error("Failed to persist Telegram pause error: %s", audit_error)
            await callback.answer("❌ Meta не смогла остановить ad set. Подробности сохранены в логах.", show_alert=True)
            return

        audit_event = build_audit_event(
                account=account,
                event_type="MANUAL_PAUSE",
                status="SUCCESS",
                correlation_id=correlation_id,
                category="MANUAL_ACTION",
                action="PAUSE_ADSET",
                message="Ad set остановлен пользователем через Telegram.",
                before_state={"status": "ACTIVE"},
                after_state={"status": "PAUSED"},
                duration_ms=(time.perf_counter() - action_started) * 1000,
                actor_type="telegram_user",
                actor_id=user_id,
                adset_id=adset_id,
            )
        session.add(audit_event)
        try:
            await session.flush()
            audit_event_id = audit_event.id
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.error("Meta paused adset %s but Telegram audit commit failed: %s", adset_id, e)
            await callback.answer("⚠️ Meta остановила ad set, но Buyerly не сохранил историю.", show_alert=True)
            return

    await callback.answer("🛑 Ad set успешно остановлен!")
    new_text = callback.message.text + "\n\n🔴 <b>СТАТУС: Остановлен вручную по алерту 🛑</b>"
    await callback.message.edit_text(
        new_text,
        reply_markup=get_undo_action_keyboard(audit_event_id) if audit_event_id else None,
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("undo_action:"))
async def cb_undo_action(callback: CallbackQuery):
    user_id = str(callback.from_user.id)
    try:
        event_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("❌ Неверная кнопка отмены.", show_alert=True)
        return

    async with async_session_maker() as session:
        user = (
            await session.execute(
                select(User).where(User.telegram_id == user_id)
            )
        ).scalar_one_or_none()
        if not user or not user.is_approved:
            await callback.answer("⛔️ Доступ не подтверждён.", show_alert=True)
            return
        try:
            result = await undo_audit_action(
                session,
                meta_client=meta_client,
                event_id=event_id,
                actor_type="telegram_user",
                actor_id=user_id,
                owner_id=user_id,
                owner_user_id=user.id,
                is_admin=user.role == "admin",
            )
        except UndoError as error:
            await callback.answer(f"❌ {error.message}", show_alert=True)
            return

    await callback.answer("✅ Действие отменено.")
    new_text = (callback.message.text or "") + f"\n\n↩️ <b>{result['message']}</b>"
    await callback.message.edit_text(new_text, reply_markup=None, parse_mode="HTML")
