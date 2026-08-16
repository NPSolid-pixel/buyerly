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

def get_account_manage_keyboard(account_id: str, rules_enabled: bool) -> InlineKeyboardMarkup:
    """Управление конкретным кабинетом: тумблер авто-правил, настройка лимитов, удаление"""
    rules_btn_text = "🛑 Выключить авто-правила" if rules_enabled else "🛡 Включить авто-правила"
    kb = [
        [
            InlineKeyboardButton(text=rules_btn_text, callback_data=f"toggle_rules:{account_id}")
        ],
        [
            InlineKeyboardButton(text="✏️ Настроить лимиты ($)", callback_data=f"edit_limits:{account_id}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_acc:{account_id}")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_limits_preset_keyboard(account_id: str) -> InlineKeyboardMarkup:
    """Быстрые пресеты лимитов + ручной ввод"""
    kb = [
        [
            InlineKeyboardButton(text="🔹 $2.0 / $6.0 / $6.0", callback_data=f"set_preset:{account_id}:2.0:6.0:6.0"),
            InlineKeyboardButton(text="🔹 $3.0 / $8.0 / $8.0", callback_data=f"set_preset:{account_id}:3.0:8.0:8.0")
        ],
        [
            InlineKeyboardButton(text="🔹 $5.0 / $10.0 / $10.0", callback_data=f"set_preset:{account_id}:5.0:10.0:10.0"),
            InlineKeyboardButton(text="✍️ Ввести вручную", callback_data=f"manual_limits:{account_id}")
        ],
        [
            InlineKeyboardButton(text="🔙 Назад", callback_data=f"back_to_acc:{account_id}")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)
