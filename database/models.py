from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime
from database.db import Base

def utcnow():
    return datetime.now(timezone.utc)

class AppSettings(Base):
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    poll_interval_minutes = Column(Integer, default=15, nullable=False, doc="Интервал опроса кабинетов (мин)")
    admin_chat_id = Column(String, default="", nullable=False)

    def __repr__(self):
        return f"<AppSettings(interval={self.poll_interval_minutes}m)>"


class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(String, unique=True, nullable=False, doc="Facebook Ad Account ID (act_...)")
    name = Column(String, nullable=False, doc="Понятное название кабинета")
    access_token = Column(String, nullable=False, doc="User/System User Access Token")
    
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
        return f"<Account(account_id='{self.account_id}', name='{self.name}', limits=[${self.max_spend_0_leads}/${self.max_spend_1_lead}])>"


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
        return f"<StoppedAdSet(adset_id='{self.adset_id}', spend=${self.stop_spend}, leads={self.stop_leads})>"
