from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from core.config import settings

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

from sqlalchemy import text

import hashlib
import uuid

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
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
    from database.models import TelegramUser
    from sqlalchemy import select

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
            existing = res.scalar_one_or_none()
            if not existing:
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
                existing.username = u["username"]
                existing.full_name = u["full_name"]
                existing.telegram_id = u["telegram_id"]
                if not existing.password_hash:
                    existing.password_hash = hash_password(u["password"])
                if not existing.auth_token:
                    existing.auth_token = u["token"]
                existing.role = u["role"]
                existing.is_approved = True
        await session.commit()



