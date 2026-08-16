import base64
import hashlib
import hmac
import json
import logging
import secrets

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

PASSWORD_SCHEME = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 600_000


def _encode_password_part(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode_password_part(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ITERATIONS,
    )
    return "$".join(
        (
            PASSWORD_SCHEME,
            str(PASSWORD_ITERATIONS),
            _encode_password_part(salt),
            _encode_password_part(digest),
        )
    )


def verify_password(password: str, encoded_password: str) -> bool:
    if not encoded_password:
        return False

    if encoded_password.startswith(f"{PASSWORD_SCHEME}$"):
        try:
            _, iterations_raw, salt_raw, expected_raw = encoded_password.split("$", 3)
            iterations = int(iterations_raw)
            if iterations < 1 or iterations > 2_000_000:
                return False
            actual = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                _decode_password_part(salt_raw),
                iterations,
            )
            return hmac.compare_digest(actual, _decode_password_part(expected_raw))
        except (TypeError, ValueError):
            return False

    if len(encoded_password) == 64:
        legacy = hashlib.sha256(password.encode("utf-8")).hexdigest()
        return hmac.compare_digest(legacy, encoded_password)
    return False


def password_needs_rehash(encoded_password: str) -> bool:
    if not encoded_password.startswith(f"{PASSWORD_SCHEME}$"):
        return True
    try:
        return int(encoded_password.split("$", 2)[1]) < PASSWORD_ITERATIONS
    except (IndexError, ValueError):
        return True


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


async def init_schema():
    # Importing the models registers every table on Base.metadata. This makes
    # database initialization reliable for all independent process entrypoints.
    import database.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        migrated_rules = await migrate_legacy_account_rules(conn)
        if migrated_rules:
            logger.info(
                "Migrated %s account(s) from legacy rule fields to active_rules.",
                migrated_rules,
            )
        # These statements support the historical SQLite schema. PostgreSQL is
        # initialized from current metadata and must not receive SQLite defaults.
        legacy_sqlite_columns = [
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
        ]
        if conn.dialect.name == "sqlite":
            for col_sql in legacy_sqlite_columns:
                try:
                    await conn.execute(text(col_sql))
                except Exception:
                    pass

async def ensure_bootstrap_admin():
    if settings.BOOTSTRAP_ADMIN_USERNAME and settings.BOOTSTRAP_ADMIN_PASSWORD:
        from sqlalchemy import select
        from database.models import TelegramUser

        async with async_session_maker() as session:
            result = await session.execute(
                select(TelegramUser).where(
                    TelegramUser.username.ilike(settings.BOOTSTRAP_ADMIN_USERNAME)
                )
            )
            if not result.scalar_one_or_none():
                session.add(
                    TelegramUser(
                        telegram_id=settings.ADMIN_CHAT_ID or None,
                        username=settings.BOOTSTRAP_ADMIN_USERNAME,
                        full_name=settings.BOOTSTRAP_ADMIN_USERNAME,
                        password_hash=hash_password(settings.BOOTSTRAP_ADMIN_PASSWORD),
                        auth_token=secrets.token_urlsafe(32),
                        role="admin",
                        is_approved=True,
                    )
                )
                await session.commit()
                logger.info("Created bootstrap admin from environment configuration.")


async def ensure_default_settings():
    from sqlalchemy import select
    from database.models import AppSettings

    async with async_session_maker() as session:
        result = await session.execute(select(AppSettings).limit(1))
        if not result.scalar_one_or_none():
            session.add(AppSettings(poll_interval_minutes=10))
            await session.commit()


async def init_db():
    await init_schema()
    await ensure_bootstrap_admin()
    await ensure_default_settings()
