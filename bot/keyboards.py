from typing import Optional
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from core.config import settings


def get_main_menu_keyboard(is_admin: bool = False) -> ReplyKeyboardMarkup:
    """Главное меню команд бота с кнопкой запуска Web App"""
    kb = []
    
    if settings.WEBAPP_URL:
        kb.append([KeyboardButton(text="🚀 Открыть Buyerly App", web_app=WebAppInfo(url=settings.WEBAPP_URL))])
        
    kb.extend([
        [
            KeyboardButton(text="📊 Сводка"),
            KeyboardButton(text="💵 Расходы")
        ],
        [
            KeyboardButton(text="🏢 Мои кабинеты"),
            KeyboardButton(text="⚙️ Настройки")
        ],
        [
            KeyboardButton(text="➕ Добавить кабинеты"),
            KeyboardButton(text="🔑 Инструкция по токену")
        ]
    ])
    if is_admin:
        kb.append([KeyboardButton(text="👑 Админ-панель")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_webapp_inline_keyboard() -> Optional[InlineKeyboardMarkup]:
    """Инлайн кнопка для мгновенного перехода в Web App"""
    if settings.WEBAPP_URL:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Открыть веб-панель", web_app=WebAppInfo(url=settings.WEBAPP_URL))]
        ])
    return None


def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура отмены пошагового мастера"""
    kb = [[KeyboardButton(text="❌ Отменить добавление")]]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_admin_approval_keyboard(telegram_id: str) -> InlineKeyboardMarkup:
    """Инлайн-кнопки одобрения нового пользователя администратором"""
    kb = [
        [
            InlineKeyboardButton(text="✅ Одобрить доступ", callback_data=f"approve_user:{telegram_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_user:{telegram_id}")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_period_keyboard() -> InlineKeyboardMarkup:
    """Выбор периода для аналитической сводки"""
    kb = [
        [
            InlineKeyboardButton(text="📅 Сегодня", callback_data="report_period:today"),
            InlineKeyboardButton(text="⏮ Вчера", callback_data="report_period:yesterday")
        ],
        [
            InlineKeyboardButton(text="📊 За 3 дня", callback_data="report_period:last_3d"),
            InlineKeyboardButton(text="📈 За неделю (7д)", callback_data="report_period:last_7d")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_interval_keyboard(current_interval: int = 10) -> InlineKeyboardMarkup:
    """Выбор интервала проверки (10, 15, 30, 60 мин)"""
    intervals = [10, 15, 30, 60]
    buttons = []
    for m in intervals:
        label = f"✅ {m} мин" if m == current_interval else f"{m} мин"
        buttons.append(InlineKeyboardButton(text=label, callback_data=f"set_interval:{m}"))
    
    kb = [
        buttons[:2],
        buttons[2:]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_reactivate_keyboard(account_id: str, adset_id: str) -> InlineKeyboardMarkup:
    """Интерактивная кнопка для включения адсета при долете лида/реги"""
    kb = [
        [
            InlineKeyboardButton(
                text="✅ Включить адсет", 
                callback_data=f"reactivate:{account_id}:{adset_id}"
            ),
            InlineKeyboardButton(
                text="❌ Оставить выключенным", 
                callback_data=f"dismiss:{account_id}:{adset_id}"
            )
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)
def get_pause_adset_keyboard(account_id: str, adset_id: str) -> InlineKeyboardMarkup:
    """Инлайн-кнопка для ручной остановки адсета по алерту"""
    kb = [
        [
            InlineKeyboardButton(
                text="🛑 Остановить адсет вручную", 
                callback_data=f"pause_adset:{account_id}:{adset_id}"
            )
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def get_undo_action_keyboard(event_id: int) -> InlineKeyboardMarkup:
    """One shared reversal action for completed Meta mutations."""

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="↩️ Отменить действие",
                    callback_data=f"undo_action:{int(event_id)}",
                )
            ]
        ]
    )

def get_account_manage_keyboard(account_id: str, rules_enabled: bool) -> InlineKeyboardMarkup:
    """Управление конкретным кабинетом: тумблер авто-правил, настройка лимитов, удаление"""
    rules_btn_text = "🛑 Выключить авто-правила" if rules_enabled else "🛡 Включить авто-правила"
    kb = [
        [
            InlineKeyboardButton(text=rules_btn_text, callback_data=f"toggle_rules:{account_id}")
        ],
        [
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_acc:{account_id}")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def get_admin_panel_keyboard() -> InlineKeyboardMarkup:
    """Кнопки в админ-панели управления"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📧 Разрешенные Email", callback_data="admin_whitelist:1")
            ]
        ]
    )


def get_admin_whitelist_keyboard(
    emails: list,
    page: int = 1,
    total_pages: int = 1,
) -> InlineKeyboardMarkup:
    """Клавиатура управления белым списком Email с пагинацией и безопасным callback_data"""
    kb = []

    # Individual delete buttons for emails on current page
    for item in emails:
        comment_str = f" ({item.comment[:15]})" if item.comment else ""
        btn_text = f"🗑 {item.email[:25]}{comment_str}"
        kb.append([
            InlineKeyboardButton(
                text=btn_text,
                callback_data=f"del_em:{item.id}",
            )
        ])

    # Pagination row
    nav_row = []
    if page > 1:
        nav_row.append(
            InlineKeyboardButton(text="◀️ Назад", callback_data=f"admin_whitelist:{page - 1}")
        )
    nav_row.append(
        InlineKeyboardButton(text=f"Стр. {page}/{total_pages}", callback_data="noop")
    )
    if page < total_pages:
        nav_row.append(
            InlineKeyboardButton(text="Вперед ▶️", callback_data=f"admin_whitelist:{page + 1}")
        )
    if len(nav_row) > 1 or total_pages > 1:
        kb.append(nav_row)

    # Action buttons
    kb.append([
        InlineKeyboardButton(text="➕ Добавить Email", callback_data="admin_add_email")
    ])
    kb.append([
        InlineKeyboardButton(text="🔙 Назад в Админ-панель", callback_data="admin_back_to_panel")
    ])

    return InlineKeyboardMarkup(inline_keyboard=kb)

