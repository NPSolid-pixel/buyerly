import hashlib
import json
import logging

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from core.config import settings

logger = logging.getLogger(__name__)

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
)

async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

Base = declarative_base()

async def get_db():
    async with async_session_maker() as session:
        yield session

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


async def migrate_legacy_account_rules(conn) -> int:
    """Add active_rules and migrate the previous single-rule account fields.

    The production database predates Account.active_rules. SQLAlchemy's
    create_all() does not add columns to existing tables, so this migration
    must run explicitly and be safe to execute at every startup.
    """

    columns = await conn.run_sync(
        lambda sync_conn: {
            column["name"]
            for column in inspect(sync_conn).get_columns("accounts")
        }
    )

    if "active_rules" not in columns:
        await conn.execute(
            text(
                "ALTER TABLE accounts "
                "ADD COLUMN active_rules TEXT NOT NULL DEFAULT '[]'"
            )
        )
        columns.add("active_rules")

    legacy_columns = {
        "preset_id",
        "preset_name",
        "rule_action",
        "rule_conditions",
        "rule_condition_logic",
        "rule_cooldown_minutes",
        "rule_check_interval",
        "rule_notify_tg",
        "rule_budget_change_percent",
        "rule_budget_max_daily",
    }
    if not legacy_columns.issubset(columns):
        return 0

    result = await conn.execute(
        text(
            """
            SELECT
                id,
                preset_id,
                preset_name,
                rule_action,
                rule_conditions,
                rule_condition_logic,
                rule_cooldown_minutes,
                rule_check_interval,
                rule_notify_tg,
                rule_budget_change_percent,
                rule_budget_max_daily
            FROM accounts
            WHERE preset_id IS NOT NULL
              AND (
                    active_rules IS NULL
                    OR TRIM(active_rules) = ''
                    OR TRIM(active_rules) = '[]'
                  )
            """
        )
    )

    migrated_count = 0
    for row in result.mappings():
        try:
            conditions = json.loads(row.get("rule_conditions") or "[]")
        except (TypeError, json.JSONDecodeError):
            conditions = []
        if not isinstance(conditions, list):
            conditions = []

        rule = {
            "preset_id": row["preset_id"],
            "name": row.get("preset_name") or f"Preset #{row['preset_id']}",
            "action": row.get("rule_action") or "turn_off",
            "conditions": conditions,
            "logic": row.get("rule_condition_logic") or "and",
            "cooldown_minutes": int(row.get("rule_cooldown_minutes") or 0),
            "check_interval": int(row.get("rule_check_interval") or 5),
            "notify_tg": (
                True
                if row.get("rule_notify_tg") is None
                else bool(row.get("rule_notify_tg"))
            ),
            "budget_change_percent": float(
                row.get("rule_budget_change_percent") or 0.0
            ),
            "budget_max_daily": float(row.get("rule_budget_max_daily") or 0.0),
        }
        await conn.execute(
            text("UPDATE accounts SET active_rules = :rules WHERE id = :account_id"),
            {
                "rules": json.dumps([rule], ensure_ascii=False),
                "account_id": row["id"],
            },
        )
        migrated_count += 1

    return migrated_count


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        migrated_rules = await migrate_legacy_account_rules(conn)
        if migrated_rules:
            logger.info(
                "Migrated %s account(s) from legacy rule fields to active_rules.",
                migrated_rules,
            )
        # Безопасное добавление колонок если таблица уже существовала
        for col_sql in [
            "ALTER TABLE telegram_users ADD COLUMN password_hash VARCHAR DEFAULT '';",
            "ALTER TABLE telegram_users ADD COLUMN auth_token VARCHAR;",
            "ALTER TABLE accounts ADD COLUMN rules_enabled BOOLEAN DEFAULT 0;",
            "ALTER TABLE accounts ADD COLUMN account_status INTEGER DEFAULT 1;",
            "ALTER TABLE accounts ADD COLUMN status_label VARCHAR DEFAULT '🟢 Активен (ACTIVE)';",
            "ALTER TABLE accounts ADD COLUMN preset_id INTEGER;",
            "ALTER TABLE accounts ADD COLUMN preset_name VARCHAR DEFAULT '';",
            "ALTER TABLE accounts ADD COLUMN rule_action VARCHAR DEFAULT 'turn_off';",
            "ALTER TABLE accounts ADD COLUMN rule_conditions TEXT DEFAULT '[]';",
            "ALTER TABLE accounts ADD COLUMN rule_condition_logic VARCHAR DEFAULT 'and';",
            "ALTER TABLE accounts ADD COLUMN rule_cooldown_minutes INTEGER DEFAULT 0;",
            "ALTER TABLE accounts ADD COLUMN rule_check_interval INTEGER DEFAULT 5;",
            "ALTER TABLE accounts ADD COLUMN rule_notify_tg BOOLEAN DEFAULT 1;",
            "ALTER TABLE accounts ADD COLUMN rule_budget_change_percent FLOAT DEFAULT 0.0;",
            "ALTER TABLE accounts ADD COLUMN rule_budget_max_daily FLOAT DEFAULT 0.0;",
            "ALTER TABLE rule_presets ADD COLUMN cooldown_minutes INTEGER DEFAULT 0;",
            "ALTER TABLE rule_presets ADD COLUMN check_interval_minutes INTEGER DEFAULT 5;",
            "ALTER TABLE rule_presets ADD COLUMN notify_tg BOOLEAN DEFAULT 1;",
            "ALTER TABLE rule_presets ADD COLUMN condition_logic VARCHAR DEFAULT 'and';",
            "ALTER TABLE rule_presets ADD COLUMN budget_change_percent FLOAT DEFAULT 0.0;",
            "ALTER TABLE rule_presets ADD COLUMN budget_max_daily FLOAT DEFAULT 0.0;"
        ]:
            try:
                await conn.execute(text(col_sql))
            except Exception:
                pass

    # Seed default users Artem and Nikolai with correct Telegram IDs
    from sqlalchemy import select

    from database.models import TelegramUser

    async with async_session_maker() as session:
        default_users = [
            {"username": "Artem", "full_name": "Artem", "telegram_id": "8634201356", "password": "artem_buyer_2026", "role": "admin", "token": "artem-token-2026-auth"},
            {"username": "Nikolai", "full_name": "Nikolai", "telegram_id": "8948797431", "password": "nikolai_buyer_2026", "role": "admin", "token": "nikolai-token-2026-auth"}
        ]
        for u in default_users:
            stmt = select(TelegramUser).where(
                (TelegramUser.username.ilike(u["username"])) |
                (TelegramUser.telegram_id == u["telegram_id"])
            )
            res = await session.execute(stmt)
            matches = res.scalars().all()
            if not matches:
                new_u = TelegramUser(
                    telegram_id=u["telegram_id"],
                    username=u["username"],
                    full_name=u["full_name"],
                    password_hash=hash_password(u["password"]),
                    auth_token=u["token"],
                    role=u["role"],
                    is_approved=True
                )
                session.add(new_u)
            else:
                primary = next((m for m in matches if m.telegram_id == u["telegram_id"]), matches[0])
                primary.username = u["username"]
                primary.full_name = u["full_name"]
                primary.telegram_id = u["telegram_id"]
                if not primary.password_hash:
                    primary.password_hash = hash_password(u["password"])
                if not primary.auth_token:
                    primary.auth_token = u["token"]
                primary.role = u["role"]
                primary.is_approved = True

                for other in matches:
                    if other.id != primary.id:
                        await session.delete(other)
        await session.commit()

