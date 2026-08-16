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
    
    # Индивидуальные ступенчатые лимиты правил (в USD $)
    max_spend_0_leads = Column(Float, default=2.0, nullable=False, doc="Макс спенд при 0 лидов/рег ($)")
    max_spend_1_lead = Column(Float, default=6.0, nullable=False, doc="Макс спенд при 1 лиде/реге ($)")
    max_cpa_multiple_leads = Column(Float, default=6.0, nullable=False, doc="Макс CPA при 2+ лидах/регах ($)")
    
    # Тип целевых действий: 'all' (лиды + реги), 'leads', 'registrations'
    conversion_event = Column(String, default="all", nullable=False)
    
    # Флаг: включать автоматически или слать кнопку с подтверждением
    auto_reactivate = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False, doc="Включен ли мониторинг")
    created_at = Column(DateTime, default=utcnow, nullable=False)

    def __repr__(self):
        return f"<Account(account_id='{self.account_id}', name='{self.name}', owner='{self.owner_id}')>"


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
