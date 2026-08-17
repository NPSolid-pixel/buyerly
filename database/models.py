from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Text, ForeignKey, Index, UniqueConstraint
from database.db import Base

def utcnow():
    return datetime.now(timezone.utc)

class TelegramUser(Base):
    __tablename__ = "telegram_users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(String, unique=True, nullable=True, index=True, doc="Telegram User ID (для пушей)")
    username = Column(String, unique=True, nullable=False, index=True, doc="Логин пользователя (e.g. Artem)")
    full_name = Column(String, default="", nullable=False)
    password_hash = Column(String, default="", nullable=False, doc="Версионированный защищённый хэш пароля")
    auth_token = Column(String, unique=True, nullable=True, index=True, doc="Постоянный токен авторизации веб-интерфейса")
    role = Column(String, default="admin", nullable=False, doc="'admin' или 'buyer'")
    is_approved = Column(Boolean, default=True, nullable=False, doc="Одобрен ли доступ")
    created_at = Column(DateTime, default=utcnow, nullable=False)

    def __repr__(self):
        return f"<TelegramUser(username='{self.username}', role='{self.role}', approved={self.is_approved})>"



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
    owner_id = Column(String, nullable=False, index=True, doc="Legacy-метка владельца для совместимости")
    owner_user_id = Column(Integer, ForeignKey("telegram_users.id"), nullable=True, index=True)
    name = Column(String, nullable=False, doc="Название пресета (e.g. 'Стоп CPL выше порога')")
    action = Column(String, default="turn_off", nullable=False, doc="'turn_off', 'turn_on', 'notify_only', 'increase_budget', 'decrease_budget'")
    conditions = Column(Text, default="[]", nullable=False, doc="JSON список условий")
    condition_logic = Column(String, default="and", nullable=False, doc="'and' или 'or' — логика объединения условий")
    cooldown_minutes = Column(Integer, default=0, nullable=False, doc="Пауза между срабатываниями (мин, 0=нет)")
    check_interval_minutes = Column(Integer, default=5, nullable=False, doc="Интервал проверки воркером (мин)")
    notify_tg = Column(Boolean, default=True, nullable=False, doc="Уведомление в Telegram")
    budget_change_percent = Column(Float, default=0.0, nullable=False, doc="На сколько % изменить бюджет")
    budget_max_daily = Column(Float, default=0.0, nullable=False, doc="Макс. дневной бюджет в валюте кабинета, 0 = без ограничения")
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    def __repr__(self):
        return f"<RulePreset(id={self.id}, name='{self.name}', action='{self.action}')>"


class RuleGroup(Base):
    """A reusable, ordered bundle of rule presets owned by one buyer."""

    __tablename__ = "rule_groups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(String, nullable=False, index=True)
    owner_user_id = Column(Integer, ForeignKey("telegram_users.id"), nullable=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String, default="", nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    def __repr__(self):
        return f"<RuleGroup(id={self.id}, name='{self.name}', owner='{self.owner_id}')>"


class RuleGroupItem(Base):
    """Ordered membership table between groups and executable presets."""

    __tablename__ = "rule_group_items"
    __table_args__ = (
        UniqueConstraint("group_id", "preset_id", name="uq_rule_group_preset"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(Integer, ForeignKey("rule_groups.id", ondelete="CASCADE"), nullable=False, index=True)
    preset_id = Column(Integer, ForeignKey("rule_presets.id", ondelete="CASCADE"), nullable=False, index=True)
    position = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    def __repr__(self):
        return f"<RuleGroupItem(group={self.group_id}, preset={self.preset_id}, position={self.position})>"


class RuleExamplesBootstrap(Base):
    """One-time marker so deleted examples are never silently recreated."""

    __tablename__ = "rule_examples_bootstrap"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_user_id = Column(
        Integer,
        ForeignKey("telegram_users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    owner_id = Column(String, default="", nullable=False)
    version = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    def __repr__(self):
        return f"<RuleExamplesBootstrap(owner_user_id={self.owner_user_id}, version={self.version})>"


class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(String, unique=True, nullable=False, index=True, doc="Facebook Ad Account ID (act_...)")
    name = Column(String, nullable=False, doc="Понятное название кабинета")
    access_token = Column(String, nullable=False, doc="User/System User Access Token")
    
    # Привязка к владельцу (мульти-пользовательская изоляция)
    owner_id = Column(String, nullable=False, index=True, doc="Legacy-метка владельца для совместимости")
    owner_user_id = Column(Integer, ForeignKey("telegram_users.id"), nullable=True, index=True)
    batch_name = Column(String, default="", nullable=False, doc="Имя пачки кабинетов (если добавлялось пачкой)")
    currency = Column(String, default="UNKNOWN", nullable=False, doc="ISO 4217 валюта рекламного кабинета из Meta")
    
    # Часовой пояс и отслеживание старта нового дня
    timezone_name = Column(String, default="UTC", nullable=False, doc="Часовой пояс рекламного кабинета")
    last_started_date = Column(String, default="", nullable=False, doc="Дата последнего зафиксированного старта открута")
    
    # Привязанные правила (JSON)
    active_rules = Column(Text, default="[]", nullable=False, doc="JSON массив объектов привязанных правил")
    
    # Статус кабинета в Meta
    account_status = Column(Integer, default=1, nullable=False, doc="1: ACTIVE, 2: DISABLED, 3: UNSETTLED")
    status_label = Column(String, default="🟢 Активен (ACTIVE)", nullable=False)
    
    rules_enabled = Column(Boolean, default=False, nullable=False, doc="Включены ли авто-правила стопов")
    is_active = Column(Boolean, default=True, nullable=False, doc="Включен ли кабинет в системе")
    created_at = Column(DateTime, default=utcnow, nullable=False)

    def __repr__(self):
        return f"<Account(account_id='{self.account_id}', name='{self.name}', status={self.account_status})>"


class SummarySnapshot(Base):
    """Durable, owner-isolated history of successful dashboard refreshes."""

    __tablename__ = "summary_snapshots"
    __table_args__ = (
        Index(
            "ix_summary_snapshots_owner_period_created",
            "owner_id",
            "period",
            "created_at",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(String, nullable=False, index=True)
    owner_user_id = Column(Integer, ForeignKey("telegram_users.id"), nullable=True, index=True)
    period = Column(String, nullable=False, index=True)
    payload = Column(Text, nullable=False, doc="Безопасный JSON сводки без access token")
    schema_version = Column(Integer, default=1, nullable=False)
    generated_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)

    def __repr__(self):
        return f"<SummarySnapshot(owner='{self.owner_id}', period='{self.period}', generated='{self.generated_at}')>"


class AnalyticsViewPreference(Base):
    """Owner-isolated saved configuration for an analytics surface."""

    __tablename__ = "analytics_view_preferences"
    __table_args__ = (
        UniqueConstraint("owner_id", "scope", name="uq_analytics_view_owner_scope"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(String, nullable=False, index=True)
    owner_user_id = Column(Integer, ForeignKey("telegram_users.id"), nullable=True, index=True)
    scope = Column(String, default="summary", nullable=False, index=True)
    config = Column(Text, default="{}", nullable=False, doc="Безопасный JSON настроек представления")
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    def __repr__(self):
        return f"<AnalyticsViewPreference(owner='{self.owner_id}', scope='{self.scope}')>"


class StoppedAdSet(Base):
    __tablename__ = "stopped_adsets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(String, nullable=False, index=True)
    adset_id = Column(String, unique=True, nullable=False, index=True)
    adset_name = Column(String, nullable=False)
    
    stop_spend = Column(Float, nullable=False, doc="Спенд на момент отключения в валюте кабинета")
    stop_leads = Column(Integer, default=0, nullable=False, doc="Лиды на момент отключения")
    stop_registrations = Column(Integer, default=0, nullable=False, doc="Регистрации на момент отключения")
    
    is_resolved = Column(Boolean, default=False, nullable=False, doc="Обработан ли долет (включен/отклонен)")
    stopped_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    def __repr__(self):
        return f"<StoppedAdSet(adset_id='{self.adset_id}', spend={self.stop_spend})>"


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


class AuditEvent(Base):
    """Append-only product audit trail, independent from notification delivery."""

    __tablename__ = "audit_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(String, default="", nullable=False, index=True)
    owner_user_id = Column(Integer, ForeignKey("telegram_users.id"), nullable=True, index=True)
    actor_type = Column(String, default="system", nullable=False, index=True)
    actor_id = Column(String, default="monitoring_worker", nullable=False)
    category = Column(String, default="RULE_ACTION", nullable=False, index=True)
    event_type = Column(String, nullable=False, index=True)
    status = Column(String, default="SUCCESS", nullable=False, index=True)
    account_id = Column(String, default="", nullable=False, index=True)
    account_name = Column(String, default="", nullable=False)
    adset_id = Column(String, default="", nullable=False, index=True)
    adset_name = Column(String, default="", nullable=False)
    rule_id = Column(Integer, nullable=True, index=True)
    rule_name = Column(String, default="", nullable=False)
    action = Column(String, default="", nullable=False, index=True)
    message = Column(Text, default="", nullable=False)
    before_state = Column(Text, default="{}", nullable=False)
    after_state = Column(Text, default="{}", nullable=False)
    details = Column(Text, default="{}", nullable=False)
    correlation_id = Column(String, default="", nullable=False, index=True)
    reverts_event_id = Column(Integer, ForeignKey("audit_events.id"), nullable=True, unique=True, index=True)
    duration_ms = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False, index=True)

    def __repr__(self):
        return (
            f"<AuditEvent(type='{self.event_type}', account='{self.account_id}', "
            f"status='{self.status}', time='{self.created_at}')>"
        )


class AutomationScheduleState(Base):
    """Durable worker schedule state that survives restart and deploy."""

    __tablename__ = "automation_schedule_states"

    id = Column(Integer, primary_key=True, autoincrement=True)
    state_key = Column(String, unique=True, nullable=False, index=True)
    owner_id = Column(String, default="", nullable=False, index=True)
    owner_user_id = Column(Integer, ForeignKey("telegram_users.id"), nullable=True, index=True)
    account_id = Column(String, default="", nullable=False, index=True)
    rule_key = Column(String, default="", nullable=False, index=True)
    last_checked_at = Column(Float, default=0.0, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class RuleExecutionState(Base):
    """One durable execution slot per account/ad set/rule/action combination."""

    __tablename__ = "rule_execution_states"

    id = Column(Integer, primary_key=True, autoincrement=True)
    execution_key = Column(String, unique=True, nullable=False, index=True)
    owner_id = Column(String, default="", nullable=False, index=True)
    owner_user_id = Column(Integer, ForeignKey("telegram_users.id"), nullable=True, index=True)
    account_id = Column(String, default="", nullable=False, index=True)
    adset_id = Column(String, default="", nullable=False, index=True)
    rule_key = Column(String, default="", nullable=False, index=True)
    action = Column(String, default="", nullable=False, index=True)
    status = Column(String, default="IDLE", nullable=False, index=True)
    correlation_id = Column(String, default="", nullable=False, index=True)
    last_attempt_at = Column(Float, default=0.0, nullable=False)
    last_success_at = Column(Float, nullable=True)
    before_state = Column(Text, default="{}", nullable=False)
    after_state = Column(Text, default="{}", nullable=False)
    details = Column(Text, default="{}", nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class ActionUndoState(Base):
    """Durable one-at-a-time claim for an idempotent audit action reversal."""

    __tablename__ = "action_undo_states"

    id = Column(Integer, primary_key=True, autoincrement=True)
    original_event_id = Column(Integer, ForeignKey("audit_events.id"), unique=True, nullable=False, index=True)
    owner_id = Column(String, default="", nullable=False, index=True)
    owner_user_id = Column(Integer, ForeignKey("telegram_users.id"), nullable=True, index=True)
    status = Column(String, default="PENDING", nullable=False, index=True)
    correlation_id = Column(String, default="", nullable=False, index=True)
    attempt_count = Column(Integer, default=1, nullable=False)
    expected_state = Column(Text, default="{}", nullable=False)
    desired_state = Column(Text, default="{}", nullable=False)
    last_error = Column(Text, default="", nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
