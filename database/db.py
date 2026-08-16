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

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Безопасное добавление колонок если таблица уже существовала
        for col_sql in [
            "ALTER TABLE accounts ADD COLUMN rules_enabled BOOLEAN DEFAULT 0;",
            "ALTER TABLE accounts ADD COLUMN account_status INTEGER DEFAULT 1;",
            "ALTER TABLE accounts ADD COLUMN status_label VARCHAR DEFAULT '🟢 Активен (ACTIVE)';",
            "ALTER TABLE accounts ADD COLUMN preset_id INTEGER;",
            "ALTER TABLE accounts ADD COLUMN preset_name VARCHAR DEFAULT '';",
            "ALTER TABLE accounts ADD COLUMN rule_action VARCHAR DEFAULT 'turn_off';",
            "ALTER TABLE accounts ADD COLUMN rule_conditions TEXT DEFAULT '[]';",
            "ALTER TABLE accounts ADD COLUMN rule_cooldown_minutes INTEGER DEFAULT 0;",
            "ALTER TABLE accounts ADD COLUMN rule_check_interval INTEGER DEFAULT 5;",
            "ALTER TABLE accounts ADD COLUMN rule_notify_tg BOOLEAN DEFAULT 1;",
            "ALTER TABLE rule_presets ADD COLUMN cooldown_minutes INTEGER DEFAULT 0;",
            "ALTER TABLE rule_presets ADD COLUMN check_interval_minutes INTEGER DEFAULT 5;",
            "ALTER TABLE rule_presets ADD COLUMN notify_tg BOOLEAN DEFAULT 1;"
        ]:
            try:
                await conn.execute(text(col_sql))
            except Exception:
                pass
