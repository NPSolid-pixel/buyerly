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
from core.metrics import (
    normalize_rule_conditions,
    normalize_runtime_rule,
    validate_runtime_rule,
)

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


async def migrate_rule_metric_contract(conn) -> dict[str, int]:
    """Normalize CPR and safely disable rules that used the removed combined CPA."""

    table_names = await conn.run_sync(
        lambda sync_conn: set(inspect(sync_conn).get_table_names())
    )
    counts = {"presets_updated": 0, "account_rules_updated": 0, "rules_disabled": 0}

    if "rule_presets" in table_names:
        rows = (
            await conn.execute(text("SELECT id, conditions FROM rule_presets"))
        ).mappings().all()
        for row in rows:
            try:
                raw_conditions = json.loads(row.get("conditions") or "[]")
            except (TypeError, json.JSONDecodeError):
                raw_conditions = []
            conditions, changed, _ = normalize_rule_conditions(raw_conditions)
            if changed:
                await conn.execute(
                    text("UPDATE rule_presets SET conditions = :conditions WHERE id = :id"),
                    {
                        "conditions": json.dumps(conditions, ensure_ascii=False),
                        "id": row["id"],
                    },
                )
                counts["presets_updated"] += 1

    if "accounts" in table_names:
        account_columns = await conn.run_sync(
            lambda sync_conn: {
                column["name"]
                for column in inspect(sync_conn).get_columns("accounts")
            }
        )
        if "active_rules" in account_columns:
            rows = (
                await conn.execute(text("SELECT id, active_rules FROM accounts"))
            ).mappings().all()
            for row in rows:
                try:
                    raw_rules = json.loads(row.get("active_rules") or "[]")
                except (TypeError, json.JSONDecodeError):
                    raw_rules = []
                if not isinstance(raw_rules, list):
                    raw_rules = []
                normalized_rules = []
                account_changed = False
                for raw_rule in raw_rules:
                    if not isinstance(raw_rule, dict):
                        account_changed = True
                        continue
                    rule, changed, disabled = normalize_runtime_rule(raw_rule)
                    normalized_rules.append(rule)
                    account_changed = account_changed or changed
                    if disabled and changed:
                        counts["rules_disabled"] += 1
                if account_changed:
                    await conn.execute(
                        text("UPDATE accounts SET active_rules = :rules WHERE id = :id"),
                        {
                            "rules": json.dumps(normalized_rules, ensure_ascii=False),
                            "id": row["id"],
                        },
                    )
                    counts["account_rules_updated"] += 1

    return counts


async def migrate_rule_safety_contract(conn) -> int:
    """Disable invalid runtime snapshots so legacy data always fails closed."""

    table_names = await conn.run_sync(
        lambda sync_conn: set(inspect(sync_conn).get_table_names())
    )
    if "accounts" not in table_names:
        return 0
    account_columns = await conn.run_sync(
        lambda sync_conn: {
            column["name"]
            for column in inspect(sync_conn).get_columns("accounts")
        }
    )
    if "active_rules" not in account_columns:
        return 0

    rows = (
        await conn.execute(text("SELECT id, active_rules FROM accounts"))
    ).mappings().all()
    updated_count = 0
    for row in rows:
        try:
            rules = json.loads(row.get("active_rules") or "[]")
        except (TypeError, json.JSONDecodeError):
            rules = []
        if not isinstance(rules, list):
            rules = []
        changed = False
        safe_rules = []
        for raw_rule in rules:
            if not isinstance(raw_rule, dict):
                changed = True
                continue
            rule = dict(raw_rule)
            try:
                validate_runtime_rule(rule)
            except (TypeError, ValueError):
                review_reason = "Правило отключено: небезопасные или устаревшие параметры. Пересохраните его."
                if (
                    rule.get("enabled") is not False
                    or rule.get("needs_review") is not True
                    or rule.get("review_reason") != review_reason
                ):
                    rule["enabled"] = False
                    rule["needs_review"] = True
                    rule["review_reason"] = review_reason
                    changed = True
            safe_rules.append(rule)
        if changed:
            await conn.execute(
                text("UPDATE accounts SET active_rules = :rules WHERE id = :id"),
                {
                    "rules": json.dumps(safe_rules, ensure_ascii=False),
                    "id": row["id"],
                },
            )
            updated_count += 1
    return updated_count


async def migrate_audit_undo_contract(conn) -> bool:
    """Add the immutable reversal link to historical audit tables."""

    table_names = await conn.run_sync(
        lambda sync_conn: set(inspect(sync_conn).get_table_names())
    )
    if "audit_events" not in table_names:
        return False
    columns = await conn.run_sync(
        lambda sync_conn: {
            column["name"]
            for column in inspect(sync_conn).get_columns("audit_events")
        }
    )
    changed = False
    if "reverts_event_id" not in columns:
        await conn.execute(text("ALTER TABLE audit_events ADD COLUMN reverts_event_id INTEGER"))
        changed = True
    await conn.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "ix_audit_events_reverts_event_id ON audit_events (reverts_event_id)"
        )
    )
    return changed


STABLE_OWNER_TABLES = (
    "rule_presets",
    "rule_groups",
    "account_groups",
    "accounts",
    "summary_snapshots",
    "analytics_view_preferences",
    "audit_events",
    "automation_schedule_states",
    "rule_execution_states",
    "action_undo_states",
    "meta_connections",
    "meta_oauth_states",
    "meta_connection_assets",
)


async def migrate_stable_owner_contract(conn) -> dict[str, int]:
    """Backfill immutable user PKs without rewriting legacy owner labels."""

    table_names = await conn.run_sync(
        lambda sync_conn: set(inspect(sync_conn).get_table_names())
    )
    counts: dict[str, int] = {}
    for table_name in STABLE_OWNER_TABLES:
        if table_name not in table_names:
            continue
        columns = await conn.run_sync(
            lambda sync_conn, name=table_name: {
                column["name"] for column in inspect(sync_conn).get_columns(name)
            }
        )
        if "owner_id" not in columns:
            continue
        if "owner_user_id" not in columns:
            await conn.execute(
                text(f"ALTER TABLE {table_name} ADD COLUMN owner_user_id INTEGER")
            )
        await conn.execute(
            text(
                f"CREATE INDEX IF NOT EXISTS ix_{table_name}_owner_user_id "
                f"ON {table_name} (owner_user_id)"
            )
        )
        result = await conn.execute(
            text(
                f"UPDATE {table_name} SET owner_user_id = ("
                "SELECT telegram_users.id FROM telegram_users "
                f"WHERE telegram_users.telegram_id = {table_name}.owner_id LIMIT 1"
                ") WHERE owner_user_id IS NULL AND owner_id IS NOT NULL"
            )
        )
        counts[table_name] = max(0, int(result.rowcount or 0))
    return counts


async def migrate_account_currency_contract(conn) -> bool:
    """Persist account currency without silently treating legacy rows as USD."""

    table_names = await conn.run_sync(
        lambda sync_conn: set(inspect(sync_conn).get_table_names())
    )
    if "accounts" not in table_names:
        return False
    columns = await conn.run_sync(
        lambda sync_conn: {
            column["name"] for column in inspect(sync_conn).get_columns("accounts")
        }
    )
    if "currency" in columns:
        return False
    await conn.execute(
        text(
            "ALTER TABLE accounts ADD COLUMN currency VARCHAR "
            "NOT NULL DEFAULT 'UNKNOWN'"
        )
    )
    return True


async def migrate_account_day_boundary_contract(conn) -> bool:
    """Add an independent marker without reusing the old spend-based date."""

    table_names = await conn.run_sync(
        lambda sync_conn: set(inspect(sync_conn).get_table_names())
    )
    if "accounts" not in table_names:
        return False
    columns = await conn.run_sync(
        lambda sync_conn: {
            column["name"] for column in inspect(sync_conn).get_columns("accounts")
        }
    )
    if "last_day_start_date" in columns:
        return False
    await conn.execute(
        text(
            "ALTER TABLE accounts ADD COLUMN last_day_start_date VARCHAR "
            "NOT NULL DEFAULT ''"
        )
    )
    return True


async def migrate_meta_connection_contract(conn) -> bool:
    """Link legacy account rows to encrypted OAuth connections when available."""

    table_names = await conn.run_sync(
        lambda sync_conn: set(inspect(sync_conn).get_table_names())
    )
    if "accounts" not in table_names:
        return False
    columns = await conn.run_sync(
        lambda sync_conn: {
            column["name"] for column in inspect(sync_conn).get_columns("accounts")
        }
    )
    if "meta_connection_id" in columns:
        return False
    await conn.execute(
        text("ALTER TABLE accounts ADD COLUMN meta_connection_id INTEGER")
    )
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_accounts_meta_connection_id "
            "ON accounts (meta_connection_id)"
        )
    )
    return True


async def migrate_account_profile_contract(conn) -> bool:
    """Add owner-editable labels without overloading the Meta account name."""

    table_names = await conn.run_sync(
        lambda sync_conn: set(inspect(sync_conn).get_table_names())
    )
    if "accounts" not in table_names:
        return False
    columns = await conn.run_sync(
        lambda sync_conn: {
            column["name"] for column in inspect(sync_conn).get_columns("accounts")
        }
    )
    changed = False
    if "custom_name" not in columns:
        await conn.execute(
            text(
                "ALTER TABLE accounts ADD COLUMN custom_name VARCHAR "
                "NOT NULL DEFAULT ''"
            )
        )
        changed = True
    if "note" not in columns:
        await conn.execute(
            text(
                "ALTER TABLE accounts ADD COLUMN note TEXT "
                "NOT NULL DEFAULT ''"
            )
        )
        changed = True
    return changed


async def migrate_automation_settings_contract(conn) -> list[str]:
    """Add safe Meta polling controls to installations created before this release."""

    table_names = await conn.run_sync(
        lambda sync_conn: set(inspect(sync_conn).get_table_names())
    )
    if "app_settings" not in table_names:
        return []
    columns = await conn.run_sync(
        lambda sync_conn: {
            column["name"] for column in inspect(sync_conn).get_columns("app_settings")
        }
    )
    definitions = {
        "critical_rule_interval_minutes": "INTEGER NOT NULL DEFAULT 2",
        "stop_confirmation_minutes": "INTEGER NOT NULL DEFAULT 10",
        "inventory_cache_minutes": "INTEGER NOT NULL DEFAULT 5",
        "account_health_interval_minutes": "INTEGER NOT NULL DEFAULT 15",
        "max_concurrent_accounts": "INTEGER NOT NULL DEFAULT 3",
        "max_concurrent_actions": "INTEGER NOT NULL DEFAULT 3",
        "usage_soft_limit_percent": "INTEGER NOT NULL DEFAULT 60",
        "usage_hard_limit_percent": "INTEGER NOT NULL DEFAULT 80",
        "adaptive_polling_enabled": "BOOLEAN NOT NULL DEFAULT TRUE",
    }
    added = []
    for name, definition in definitions.items():
        if name in columns:
            continue
        await conn.execute(
            text(f"ALTER TABLE app_settings ADD COLUMN {name} {definition}")
        )
        added.append(name)
    return added


async def migrate_workspaces_contract(conn) -> int:
    """Ensure workspaces table exists and backfill a default workspace for each user."""
    from datetime import datetime, timezone

    table_names = await conn.run_sync(
        lambda sync_conn: set(inspect(sync_conn).get_table_names())
    )
    if "workspaces" not in table_names or "telegram_users" not in table_names:
        return 0

    user_columns = await conn.run_sync(
        lambda sync_conn: {
            column["name"] for column in inspect(sync_conn).get_columns("telegram_users")
        }
    )
    if "active_workspace_id" not in user_columns:
        await conn.execute(text("ALTER TABLE telegram_users ADD COLUMN active_workspace_id INTEGER"))

    for tbl in ("rule_presets", "rule_groups", "account_groups", "accounts", "meta_connections", "summary_snapshots", "audit_events"):
        if tbl in table_names:
            cols = await conn.run_sync(
                lambda sync_conn, name=tbl: {column["name"] for column in inspect(sync_conn).get_columns(name)}
            )
            if "workspace_id" not in cols:
                await conn.execute(text(f"ALTER TABLE {tbl} ADD COLUMN workspace_id INTEGER"))
                await conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{tbl}_workspace_id ON {tbl} (workspace_id)"))

    users = (await conn.execute(text("SELECT id, username FROM telegram_users"))).mappings().all()
    created_workspaces = 0
    for u in users:
        u_id = u["id"]
        has_membership = (await conn.execute(
            text("SELECT workspace_id FROM workspace_members WHERE user_id = :uid LIMIT 1"),
            {"uid": u_id}
        )).scalar()

        if not has_membership:
            ws_name = "Buyerly"
            ws_slug = "buyerly"
            existing_slug = (await conn.execute(
                text("SELECT id FROM workspaces WHERE slug = :slug"),
                {"slug": ws_slug}
            )).scalar()
            if existing_slug:
                ws_slug = f"buyerly-{u_id}"

            now_dt = datetime.now(timezone.utc).replace(tzinfo=None)
            if conn.dialect.name == "postgresql":
                res = await conn.execute(
                    text(
                        "INSERT INTO workspaces (name, slug, badge_text, badge_color, owner_user_id, created_at, updated_at) "
                        "VALUES (:name, :slug, :badge_text, :badge_color, :owner_user_id, :now, :now) RETURNING id"
                    ),
                    {"name": ws_name, "slug": ws_slug, "badge_text": "B", "badge_color": "#F5A300", "owner_user_id": u_id, "now": now_dt}
                )
                ws_id = res.scalar()
            else:
                await conn.execute(
                    text(
                        "INSERT INTO workspaces (name, slug, badge_text, badge_color, owner_user_id, created_at, updated_at) "
                        "VALUES (:name, :slug, :badge_text, :badge_color, :owner_user_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    ),
                    {"name": ws_name, "slug": ws_slug, "badge_text": "B", "badge_color": "#F5A300", "owner_user_id": u_id}
                )
                ws_id = (await conn.execute(text("SELECT last_insert_rowid()"))).scalar()

            if conn.dialect.name == "postgresql":
                await conn.execute(
                    text("INSERT INTO workspace_members (workspace_id, user_id, role, joined_at) VALUES (:ws_id, :u_id, 'owner', :now)"),
                    {"ws_id": ws_id, "u_id": u_id, "now": now_dt}
                )
            else:
                await conn.execute(
                    text("INSERT INTO workspace_members (workspace_id, user_id, role, joined_at) VALUES (:ws_id, :u_id, 'owner', CURRENT_TIMESTAMP)"),
                    {"ws_id": ws_id, "u_id": u_id}
                )

            await conn.execute(
                text("UPDATE telegram_users SET active_workspace_id = :ws_id WHERE id = :u_id"),
                {"ws_id": ws_id, "u_id": u_id}
            )
            created_workspaces += 1
            has_membership = ws_id

        await conn.execute(
            text("UPDATE telegram_users SET active_workspace_id = :ws_id WHERE id = :u_id AND active_workspace_id IS NULL"),
            {"ws_id": has_membership, "u_id": u_id}
        )

        for tbl in ("rule_presets", "rule_groups", "account_groups", "accounts", "meta_connections", "summary_snapshots", "audit_events"):
            if tbl in table_names:
                await conn.execute(
                    text(f"UPDATE {tbl} SET workspace_id = :ws_id WHERE owner_user_id = :u_id AND workspace_id IS NULL"),
                    {"ws_id": has_membership, "u_id": u_id}
                )

    return created_workspaces


async def migrate_rule_groups_position(conn) -> bool:
    """Add position column to rule_groups table if missing."""
    table_names = await conn.run_sync(
        lambda sync_conn: set(inspect(sync_conn).get_table_names())
    )
    if "rule_groups" not in table_names:
        return False
    columns = await conn.run_sync(
        lambda sync_conn: {
            column["name"] for column in inspect(sync_conn).get_columns("rule_groups")
        }
    )
    if "position" not in columns:
        await conn.execute(text("ALTER TABLE rule_groups ADD COLUMN position INTEGER NOT NULL DEFAULT 0"))
        return True
    return False


async def init_schema():
    # Importing the models registers every table on Base.metadata. This makes
    # database initialization reliable for all independent process entrypoints.
    import database.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if await migrate_rule_groups_position(conn):
            logger.info("Added position column to rule_groups.")
        audit_undo_migrated = await migrate_audit_undo_contract(conn)
        if audit_undo_migrated:
            logger.info("Added immutable audit reversal links.")
        owner_migration = await migrate_stable_owner_contract(conn)
        if any(owner_migration.values()):
            logger.info("Backfilled stable ownership: %s", owner_migration)
        if await migrate_account_currency_contract(conn):
            logger.info("Added the persisted account currency contract.")
        if await migrate_account_day_boundary_contract(conn):
            logger.info("Added the account-local day boundary contract.")
        if await migrate_meta_connection_contract(conn):
            logger.info("Added encrypted Meta connection links to accounts.")
        if await migrate_account_profile_contract(conn):
            logger.info("Added editable Buyerly account names and notes.")
        automation_settings_added = await migrate_automation_settings_contract(conn)
        if automation_settings_added:
            logger.info(
                "Added automation polling controls: %s",
                ", ".join(automation_settings_added),
            )
        migrated_workspaces = await migrate_workspaces_contract(conn)
        if migrated_workspaces:
            logger.info("Backfilled default workspace for %s user(s).", migrated_workspaces)
        migrated_rules = await migrate_legacy_account_rules(conn)
        if migrated_rules:
            logger.info(
                "Migrated %s account(s) from legacy rule fields to active_rules.",
                migrated_rules,
            )
        metric_migration = await migrate_rule_metric_contract(conn)
        if any(metric_migration.values()):
            logger.info("Migrated rule metric contract: %s", metric_migration)
        safety_migration = await migrate_rule_safety_contract(conn)
        if safety_migration:
            logger.warning(
                "Disabled unsafe runtime rules for %s account(s).",
                safety_migration,
            )
        # These statements support the historical SQLite schema. PostgreSQL is
        # initialized from current metadata and must not receive SQLite defaults.
        legacy_sqlite_columns = [
            "ALTER TABLE telegram_users ADD COLUMN password_hash VARCHAR DEFAULT '';",
            "ALTER TABLE telegram_users ADD COLUMN auth_token VARCHAR;",
            "ALTER TABLE accounts ADD COLUMN rules_enabled BOOLEAN DEFAULT 0;",
            "ALTER TABLE accounts ADD COLUMN account_status INTEGER DEFAULT 1;",
            "ALTER TABLE accounts ADD COLUMN status_label VARCHAR DEFAULT 'Активен (ACTIVE)';",
            "ALTER TABLE accounts ADD COLUMN currency VARCHAR DEFAULT 'UNKNOWN';",
            "ALTER TABLE accounts ADD COLUMN last_day_start_date VARCHAR DEFAULT '';",
            "ALTER TABLE accounts ADD COLUMN custom_name VARCHAR DEFAULT '';",
            "ALTER TABLE accounts ADD COLUMN note TEXT DEFAULT '';",
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
            "ALTER TABLE rule_presets ADD COLUMN budget_max_daily FLOAT DEFAULT 0.0;",
            "ALTER TABLE rule_groups ADD COLUMN position INTEGER DEFAULT 0;"
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
