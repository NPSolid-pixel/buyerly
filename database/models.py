from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Text
from database.db import Base

def utcnow():
    return datetime.now(timezone.utc)

class TelegramUser(Base):
    __tablename__ = "telegram_users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(String, unique=True, nullable=False, index=True, doc="Telegram User ID")
    username = Column(String, default="", nullable=False)
    full_name = Column(String, default="", nullable=False)
    role = Column(String, default="buyer", nullable=False, doc="'admin' или 'buyer'")
    is_approved = Column(Boolean, default=False, nullable=False, doc="Одобрен ли доступ админом")
    created_at = Column(DateTime, default=utcnow, nullable=False)

    def __repr__(self):
        return f"<TelegramUser(tg_id='{self.telegram_id}', username='{self.username}', role='{self.role}', approved={self.is_approved})>"


class AppSettings(Base):
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    poll_interval_minutes = Column(Integer, default=10, nullable=False, doc="Интервал опроса кабинетов (мин)")
    admin_chat_id = Column(String, default="", nullable=False)

    def __repr__(self):
        return f"<AppSettings(interval={self.poll_interval_minutes}m)>"


class RulePreset(Base):
    __tablename__ = "rule_presets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(String, nullable=False, index=True, doc="Telegram ID владельца")
    name = Column(String, nullable=False, doc="Название пресета (e.g. 'Стоп CPA > $6')")
    action = Column(String, default="turn_off", nullable=False, doc="'turn_off', 'turn_on', 'notify_only', 'increase_budget', 'decrease_budget'")
    conditions = Column(Text, default="[]", nullable=False, doc="JSON список условий")
    condition_logic = Column(String, default="and", nullable=False, doc="'and' или 'or' — логика объединения условий")
    cooldown_minutes = Column(Integer, default=0, nullable=False, doc="Пауза между срабатываниями (мин, 0=нет)")
    check_interval_minutes = Column(Integer, default=5, nullable=False, doc="Интервал проверки воркером (мин)")
    notify_tg = Column(Boolean, default=True, nullable=False, doc="Уведомление в Telegram")
    budget_change_percent = Column(Float, default=0.0, nullable=False, doc="На сколько % изменить бюджет")
    budget_max_daily = Column(Float, default=0.0, nullable=False, doc="Макс. потолок бюджета ($/день), 0 = без ограничения")
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    def __repr__(self):
        return f"<RulePreset(id={self.id}, name='{self.name}', action='{self.action}')>"


class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(String, unique=True, nullable=False, index=True, doc="Facebook Ad Account ID (act_...)")
    name = Column(String, nullable=False, doc="Понятное название кабинета")
    access_token = Column(String, nullable=False, doc="User/System User Access Token")
    
    # Привязка к владельцу (мульти-пользовательская изоляция)
    owner_id = Column(String, nullable=False, index=True, doc="Telegram ID байера-владельца")
    batch_name = Column(String, default="", nullable=False, doc="Имя пачки кабинетов (если добавлялось пачкой)")
    
    # Часовой пояс и отслеживание старта нового дня
    timezone_name = Column(String, default="UTC", nullable=False, doc="Часовой пояс рекламного кабинета")
    last_started_date = Column(String, default="", nullable=False, doc="Дата последнего зафиксированного старта открута")
    
    # Привязанный пресет и правила
    preset_id = Column(Integer, nullable=True, doc="ID привязанного пресета")
    preset_name = Column(String, default="", nullable=False, doc="Название активного пресета")
    rule_action = Column(String, default="turn_off", nullable=False, doc="turn_off, turn_on, notify_only, increase_budget, decrease_budget")
    rule_conditions = Column(Text, default="[]", nullable=False, doc="JSON список условий")
    rule_condition_logic = Column(String, default="and", nullable=False, doc="'and' или 'or' — логика объединения условий")
    rule_cooldown_minutes = Column(Integer, default=0, nullable=False, doc="Пауза между срабатываниями (мин)")
    rule_check_interval = Column(Integer, default=5, nullable=False, doc="Интервал проверки (мин)")
    rule_notify_tg = Column(Boolean, default=True, nullable=False, doc="Уведомление в Telegram")
    rule_budget_change_percent = Column(Float, default=0.0, nullable=False, doc="На сколько % изменить бюджет")
    rule_budget_max_daily = Column(Float, default=0.0, nullable=False, doc="Макс. потолок бюджета ($/день), 0 = без ограничения")
    
    # Статус кабинета в Meta
    account_status = Column(Integer, default=1, nullable=False, doc="1: ACTIVE, 2: DISABLED, 3: UNSETTLED")
    status_label = Column(String, default="🟢 Активен (ACTIVE)", nullable=False)
    
    rules_enabled = Column(Boolean, default=False, nullable=False, doc="Включены ли авто-правила стопов")
    is_active = Column(Boolean, default=True, nullable=False, doc="Включен ли кабинет в системе")
    created_at = Column(DateTime, default=utcnow, nullable=False)

    def __repr__(self):
        return f"<Account(account_id='{self.account_id}', name='{self.name}', status={self.account_status})>"


class StoppedAdSet(Base):
    __tablename__ = "stopped_adsets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(String, nullable=False, index=True)
    adset_id = Column(String, unique=True, nullable=False, index=True)
    adset_name = Column(String, nullable=False)
    
    stop_spend = Column(Float, nullable=False, doc="Спенд на момент отключения ($)")
    stop_leads = Column(Integer, default=0, nullable=False, doc="Лиды на момент отключения")
    stop_registrations = Column(Integer, default=0, nullable=False, doc="Регистрации на момент отключения")
    
    is_resolved = Column(Boolean, default=False, nullable=False, doc="Обработан ли долет (включен/отклонен)")
    stopped_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    def __repr__(self):
        return f"<StoppedAdSet(adset_id='{self.adset_id}', spend=${self.stop_spend})>"


class EventLog(Base):
    __tablename__ = "event_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String, nullable=False, index=True, doc="Тип события (ALERT_SENT, DAY_START, STOP, etc.)")
    target_chat_id = Column(String, default="", index=True, doc="Кому отправлено (Telegram ID)")
    account_id = Column(String, default="", index=True, doc="ID кабинета")
    message = Column(Text, nullable=False, doc="Текст отправленного сообщения или события")
    status = Column(String, default="SUCCESS", nullable=False, doc="Статус: SUCCESS или ERROR")
    created_at = Column(DateTime, default=utcnow, nullable=False, index=True)

    def __repr__(self):
        return f"<EventLog(type='{self.event_type}', chat='{self.target_chat_id}', status='{self.status}', time='{self.created_at}')>"
