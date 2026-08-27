import base64
import hashlib
import hmac
import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from core.config import settings
from core.metrics import (
    normalize_rule_conditions,
    normalize_runtime_rule,
    validate_runtime_rule,
)
from database.jsonb_contract import JSONB_NATIVE_COLUMNS, decode_legacy_jsonb_string
from database.rule_workspace_contract import scope_runtime_rule_snapshots

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
        preset_columns = await conn.run_sync(
            lambda sync_conn: {
                column["name"]: column
                for column in inspect(sync_conn).get_columns("rule_presets")
            }
        )
        conditions_assignment = (
            "CAST(:conditions AS JSONB)"
            if "JSONB" in str(preset_columns["conditions"]["type"]).upper()
            else ":conditions"
        )
        rows = (
            await conn.execute(text("SELECT id, conditions FROM rule_presets"))
        ).mappings().all()
        for row in rows:
            raw_conditions = row.get("conditions") or []
            if isinstance(raw_conditions, str):
                try:
                    raw_conditions = json.loads(raw_conditions)
                except (TypeError, json.JSONDecodeError):
                    raw_conditions = []
            conditions, changed, _ = normalize_rule_conditions(raw_conditions)
            if changed:
                await conn.execute(
                    text(
                        "UPDATE rule_presets SET conditions = "
                        f"{conditions_assignment} WHERE id = :id"
                    ),
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
                "SELECT users.id FROM users "
                f"WHERE users.telegram_id = {table_name}.owner_id LIMIT 1"
                ") WHERE owner_user_id IS NULL AND owner_id IS NOT NULL"
            )
        )
        counts[table_name] = max(0, int(result.rowcount or 0))
    return counts


async def migrate_audit_event_ownership_contract(conn) -> dict[str, int | bool]:
    """Make legacy audit ownership compatible with workspace-scoped events.

    Older production tables still contain ``owner_id VARCHAR NOT NULL`` while
    the current ORM writes ``owner_user_id``. Keeping that legacy constraint
    makes scheduler-generated events fail even when their stable owner and
    workspace are present. The column is retained for historical reads but is
    made nullable; stable ownership is backfilled from the matching account.
    """

    table_names = await conn.run_sync(
        lambda sync_conn: set(inspect(sync_conn).get_table_names())
    )
    if "audit_events" not in table_names or "accounts" not in table_names:
        return {
            "legacy_owner_constraint_removed": False,
            "owners_backfilled": 0,
            "workspaces_backfilled": 0,
        }

    audit_columns = await conn.run_sync(
        lambda sync_conn: {
            column["name"]: column
            for column in inspect(sync_conn).get_columns("audit_events")
        }
    )
    account_columns = await conn.run_sync(
        lambda sync_conn: {
            column["name"]
            for column in inspect(sync_conn).get_columns("accounts")
        }
    )

    legacy_constraint_removed = False
    owner_column = audit_columns.get("owner_id")
    if owner_column and not owner_column.get("nullable", True):
        await conn.execute(
            text("ALTER TABLE audit_events ALTER COLUMN owner_id DROP NOT NULL")
        )
        legacy_constraint_removed = True

    owners_backfilled = 0
    if {
        "owner_user_id",
        "account_id",
    }.issubset(audit_columns) and {
        "owner_user_id",
        "account_id",
    }.issubset(account_columns):
        result = await conn.execute(
            text(
                "UPDATE audit_events AS event "
                "SET owner_user_id = account.owner_user_id "
                "FROM accounts AS account "
                "WHERE event.owner_user_id IS NULL "
                "AND event.account_id = account.account_id "
                "AND account.owner_user_id IS NOT NULL"
            )
        )
        owners_backfilled = max(0, int(result.rowcount or 0))

    workspaces_backfilled = 0
    if {
        "workspace_id",
        "account_id",
    }.issubset(audit_columns) and {
        "workspace_id",
        "account_id",
    }.issubset(account_columns):
        result = await conn.execute(
            text(
                "UPDATE audit_events AS event "
                "SET workspace_id = account.workspace_id "
                "FROM accounts AS account "
                "WHERE event.workspace_id IS NULL "
                "AND event.account_id = account.account_id "
                "AND account.workspace_id IS NOT NULL"
            )
        )
        workspaces_backfilled = max(0, int(result.rowcount or 0))

    return {
        "legacy_owner_constraint_removed": legacy_constraint_removed,
        "owners_backfilled": owners_backfilled,
        "workspaces_backfilled": workspaces_backfilled,
    }


async def migrate_jsonb_native_contract(conn) -> dict[str, int]:
    """Decode valid legacy JSON strings stored inside structured JSONB columns.

    Wrong-type and malformed values are deliberately preserved for inspection.
    Returning their count makes the migration a data-integrity preflight as
    well as an idempotent conversion.
    """

    table_names = await conn.run_sync(
        lambda sync_conn: set(inspect(sync_conn).get_table_names())
    )
    converted = 0
    malformed = 0

    for table_name, primary_key, column_name, expected_type in JSONB_NATIVE_COLUMNS:
        if table_name not in table_names:
            continue
        columns = await conn.run_sync(
            lambda sync_conn, name=table_name: {
                column["name"]: column
                for column in inspect(sync_conn).get_columns(name)
            }
        )
        column = columns.get(column_name)
        if column is None or "JSONB" not in str(column["type"]).upper():
            continue

        rows = (
            await conn.execute(
                text(
                    f"SELECT {primary_key} AS _pk, {column_name} AS _value "
                    f"FROM {table_name} "
                    f"WHERE jsonb_typeof({column_name}) <> :expected_type"
                ),
                {"expected_type": expected_type},
            )
        ).mappings().all()
        for row in rows:
            is_valid, decoded = decode_legacy_jsonb_string(
                row["_value"],
                expected_type,
            )
            if not is_valid:
                malformed += 1
                continue
            result = await conn.execute(
                text(
                    f"UPDATE {table_name} "
                    f"SET {column_name} = CAST(:native_json AS JSONB) "
                    f"WHERE {primary_key} = :primary_key "
                    f"AND jsonb_typeof({column_name}) = 'string'"
                ),
                {
                    "native_json": json.dumps(
                        decoded,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    "primary_key": row["_pk"],
                },
            )
            converted += max(0, int(result.rowcount or 0))

    return {"converted": converted, "malformed": malformed}


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
        "admin_chat_id": "VARCHAR NOT NULL DEFAULT ''",
        "updated_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
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


async def migrate_users_table_contract(conn) -> bool:
    """Safely rename legacy telegram_users table to users if it exists."""
    table_names = await conn.run_sync(
        lambda sync_conn: set(inspect(sync_conn).get_table_names())
    )
    if "telegram_users" in table_names and "users" not in table_names:
        await conn.execute(text("ALTER TABLE telegram_users RENAME TO users"))
        return True
    return False


async def migrate_workspaces_contract(conn) -> int:
    """Ensure workspaces table exists and backfill a default workspace for each user."""
    from datetime import datetime, timezone

    table_names = await conn.run_sync(
        lambda sync_conn: set(inspect(sync_conn).get_table_names())
    )
    if "workspaces" not in table_names or "users" not in table_names:
        return 0

    user_columns = await conn.run_sync(
        lambda sync_conn: {
            column["name"] for column in inspect(sync_conn).get_columns("users")
        }
    )
    if "active_workspace_id" not in user_columns:
        await conn.execute(text("ALTER TABLE users ADD COLUMN active_workspace_id INTEGER"))

    for tbl in ("rule_presets", "rule_groups", "account_groups", "accounts", "meta_connections", "summary_snapshots", "audit_events"):
        if tbl in table_names:
            cols = await conn.run_sync(
                lambda sync_conn, name=tbl: {column["name"] for column in inspect(sync_conn).get_columns(name)}
            )
            if "workspace_id" not in cols:
                await conn.execute(text(f"ALTER TABLE {tbl} ADD COLUMN workspace_id INTEGER"))
                await conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{tbl}_workspace_id ON {tbl} (workspace_id)"))

    users = (await conn.execute(text("SELECT id, username FROM users"))).mappings().all()
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

            now_dt = datetime.now(timezone.utc)
            res = await conn.execute(
                text(
                    "INSERT INTO workspaces (name, slug, badge_text, badge_color, owner_user_id, created_at, updated_at) "
                    "VALUES (:name, :slug, :badge_text, :badge_color, :owner_user_id, :now, :now) RETURNING id"
                ),
                {"name": ws_name, "slug": ws_slug, "badge_text": "B", "badge_color": "#F5A300", "owner_user_id": u_id, "now": now_dt}
            )
            ws_id = res.scalar()

            await conn.execute(
                text("INSERT INTO workspace_members (workspace_id, user_id, role, joined_at) VALUES (:ws_id, :u_id, 'owner', :now)"),
                {"ws_id": ws_id, "u_id": u_id, "now": now_dt}
            )

            await conn.execute(
                text("UPDATE users SET active_workspace_id = :ws_id WHERE id = :u_id"),
                {"ws_id": ws_id, "u_id": u_id}
            )
            created_workspaces += 1
            has_membership = ws_id

        await conn.execute(
            text("UPDATE users SET active_workspace_id = :ws_id WHERE id = :u_id AND active_workspace_id IS NULL"),
            {"ws_id": has_membership, "u_id": u_id}
        )

        for tbl in ("rule_presets", "rule_groups", "account_groups", "accounts", "meta_connections", "summary_snapshots", "audit_events"):
            if tbl in table_names:
                await conn.execute(
                    text(f"UPDATE {tbl} SET workspace_id = :ws_id WHERE owner_user_id = :u_id AND workspace_id IS NULL"),
                    {"ws_id": has_membership, "u_id": u_id}
                )

    return created_workspaces


async def migrate_rule_workspace_contract(conn) -> dict[str, int]:
    """Backfill rule workspaces and fail closed for ambiguous runtime links."""

    table_names = await conn.run_sync(
        lambda sync_conn: set(inspect(sync_conn).get_table_names())
    )
    required = {"users", "workspace_members", "rule_presets", "rule_groups"}
    if not required.issubset(table_names):
        return {
            "presets_backfilled": 0,
            "groups_backfilled": 0,
            "group_links_removed": 0,
            "snapshots_changed": 0,
            "snapshots_disabled": 0,
        }

    counts = {
        "presets_backfilled": 0,
        "groups_backfilled": 0,
        "group_links_removed": 0,
        "snapshots_changed": 0,
        "snapshots_disabled": 0,
    }
    for table_name, count_key in (
        ("rule_presets", "presets_backfilled"),
        ("rule_groups", "groups_backfilled"),
    ):
        result = await conn.execute(
            text(
                f"""
                UPDATE {table_name} AS target
                SET workspace_id = owner.active_workspace_id
                FROM users AS owner
                WHERE target.workspace_id IS NULL
                  AND target.owner_user_id = owner.id
                  AND owner.active_workspace_id IS NOT NULL
                  AND EXISTS (
                      SELECT 1
                      FROM workspace_members AS member
                      WHERE member.user_id = owner.id
                        AND member.workspace_id = owner.active_workspace_id
                  )
                """
            )
        )
        counts[count_key] = max(result.rowcount or 0, 0)

    if "rule_group_items" in table_names:
        removed = await conn.execute(
            text(
                """
                DELETE FROM rule_group_items AS item
                USING rule_groups AS rule_group, rule_presets AS preset
                WHERE item.group_id = rule_group.id
                  AND item.preset_id = preset.id
                  AND (
                      rule_group.workspace_id IS NULL
                      OR preset.workspace_id IS NULL
                      OR rule_group.workspace_id <> preset.workspace_id
                  )
                """
            )
        )
        counts["group_links_removed"] = max(removed.rowcount or 0, 0)

    await conn.execute(
        text(
            """
            WITH ranked AS (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY workspace_id
                           ORDER BY position, id
                       ) - 1 AS scoped_position
                FROM rule_groups
                WHERE workspace_id IS NOT NULL
            )
            UPDATE rule_groups AS rule_group
            SET position = ranked.scoped_position
            FROM ranked
            WHERE rule_group.id = ranked.id
              AND rule_group.position <> ranked.scoped_position
            """
        )
    )
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_rule_groups_workspace_position "
            "ON rule_groups (workspace_id, position, id)"
        )
    )

    if "rule_examples_bootstrap" in table_names:
        marker_columns = await conn.run_sync(
            lambda sync_conn: {
                column["name"]
                for column in inspect(sync_conn).get_columns("rule_examples_bootstrap")
            }
        )
        if "workspace_id" not in marker_columns:
            await conn.execute(
                text("ALTER TABLE rule_examples_bootstrap ADD COLUMN workspace_id INTEGER")
            )
        await conn.execute(
            text(
                """
                UPDATE rule_examples_bootstrap AS marker
                SET workspace_id = owner.active_workspace_id
                FROM users AS owner
                WHERE marker.owner_user_id = owner.id
                  AND marker.workspace_id IS NULL
                  AND owner.active_workspace_id IS NOT NULL
                  AND EXISTS (
                      SELECT 1
                      FROM workspace_members AS member
                      WHERE member.user_id = owner.id
                        AND member.workspace_id = owner.active_workspace_id
                  )
                """
            )
        )
        await conn.execute(
            text("DELETE FROM rule_examples_bootstrap WHERE workspace_id IS NULL")
        )
        unique_constraints = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_unique_constraints(
                "rule_examples_bootstrap"
            )
        )
        for constraint in unique_constraints:
            if constraint.get("column_names") != ["owner_user_id"]:
                continue
            constraint_name = str(constraint.get("name") or "")
            if constraint_name:
                quoted_name = '"' + constraint_name.replace('"', '""') + '"'
                await conn.execute(
                    text(
                        "ALTER TABLE rule_examples_bootstrap "
                        f"DROP CONSTRAINT IF EXISTS {quoted_name}"
                    )
                )
        await conn.execute(
            text(
                "ALTER TABLE rule_examples_bootstrap "
                "ALTER COLUMN workspace_id SET NOT NULL"
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_rule_examples_bootstrap_workspace_id "
                "ON rule_examples_bootstrap (workspace_id)"
            )
        )
        await conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_rule_examples_ws_owner "
                "ON rule_examples_bootstrap (workspace_id, owner_user_id)"
            )
        )
        marker_foreign_keys = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_foreign_keys(
                "rule_examples_bootstrap"
            )
        )
        has_workspace_foreign_key = any(
            foreign_key.get("constrained_columns") == ["workspace_id"]
            for foreign_key in marker_foreign_keys
        )
        if not has_workspace_foreign_key and conn.dialect.name == "postgresql":
            await conn.execute(
                text(
                    "ALTER TABLE rule_examples_bootstrap "
                    "ADD CONSTRAINT fk_rule_examples_bootstrap_workspace "
                    "FOREIGN KEY (workspace_id) REFERENCES workspaces(id) "
                    "ON DELETE CASCADE"
                )
            )

    preset_workspaces = {
        int(row["id"]): row["workspace_id"]
        for row in (
            await conn.execute(text("SELECT id, workspace_id FROM rule_presets"))
        ).mappings()
    }
    if "accounts" in table_names:
        account_columns = await conn.run_sync(
            lambda sync_conn: {
                column["name"] for column in inspect(sync_conn).get_columns("accounts")
            }
        )
        if {"workspace_id", "active_rules", "rules_enabled"}.issubset(account_columns):
            account_rows = (
                await conn.execute(
                    text(
                        "SELECT id, workspace_id, active_rules, rules_enabled "
                        "FROM accounts"
                    )
                )
            ).mappings()
            for row in account_rows:
                normalized, changed, disabled = scope_runtime_rule_snapshots(
                    row["active_rules"],
                    account_workspace_id=row["workspace_id"],
                    preset_workspaces=preset_workspaces,
                )
                if not changed:
                    continue
                has_executable = any(
                    rule.get("workspace_id") == row["workspace_id"]
                    and rule.get("enabled", True) is not False
                    and rule.get("needs_review", False) is not True
                    for rule in normalized
                )
                await conn.execute(
                    text(
                        "UPDATE accounts SET active_rules = :active_rules, "
                        "rules_enabled = :rules_enabled WHERE id = :id"
                    ),
                    {
                        "id": row["id"],
                        "active_rules": json.dumps(
                            normalized,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        "rules_enabled": bool(row["rules_enabled"] and has_executable),
                    },
                )
                counts["snapshots_changed"] += 1
                counts["snapshots_disabled"] += disabled

    if conn.dialect.name == "postgresql" and "rule_group_items" in table_names:
        await conn.execute(
            text(
                """
                CREATE OR REPLACE FUNCTION enforce_rule_group_item_workspace()
                RETURNS trigger AS $$
                DECLARE
                    group_workspace INTEGER;
                    preset_workspace INTEGER;
                BEGIN
                    SELECT workspace_id INTO group_workspace
                    FROM rule_groups WHERE id = NEW.group_id;
                    SELECT workspace_id INTO preset_workspace
                    FROM rule_presets WHERE id = NEW.preset_id;
                    IF group_workspace IS NULL
                       OR preset_workspace IS NULL
                       OR group_workspace <> preset_workspace THEN
                        RAISE EXCEPTION 'rule group and preset must share workspace';
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql
                """
            )
        )
        await conn.execute(
            text(
                "DROP TRIGGER IF EXISTS trg_rule_group_item_workspace "
                "ON rule_group_items"
            )
        )
        await conn.execute(
            text(
                "CREATE TRIGGER trg_rule_group_item_workspace "
                "BEFORE INSERT OR UPDATE ON rule_group_items "
                "FOR EACH ROW EXECUTE FUNCTION enforce_rule_group_item_workspace()"
            )
        )

    return counts


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


async def migrate_onboarding_contract(conn) -> bool:
    """Ensure user profile columns and workspace logo column exist."""
    table_names = await conn.run_sync(
        lambda sync_conn: set(inspect(sync_conn).get_table_names())
    )
    migrated = False
    if "users" in table_names:
        user_columns = await conn.run_sync(
            lambda sync_conn: {
                column["name"] for column in inspect(sync_conn).get_columns("users")
            }
        )
        if "first_name" not in user_columns:
            await conn.execute(text("ALTER TABLE users ADD COLUMN first_name VARCHAR DEFAULT ''"))
            migrated = True
        if "last_name" not in user_columns:
            await conn.execute(text("ALTER TABLE users ADD COLUMN last_name VARCHAR DEFAULT ''"))
            migrated = True
        if "email" not in user_columns:
            await conn.execute(text("ALTER TABLE users ADD COLUMN email VARCHAR"))
            migrated = True
        if "avatar_url" not in user_columns:
            await conn.execute(text("ALTER TABLE users ADD COLUMN avatar_url VARCHAR DEFAULT ''"))
            migrated = True
        if "onboarding_step" not in user_columns:
            await conn.execute(text("ALTER TABLE users ADD COLUMN onboarding_step VARCHAR DEFAULT 'personal_details'"))
            migrated = True
        if "onboarding_completed" not in user_columns:
            await conn.execute(text("ALTER TABLE users ADD COLUMN onboarding_completed BOOLEAN DEFAULT FALSE"))
            migrated = True

    if "workspaces" in table_names:
        ws_columns = await conn.run_sync(
            lambda sync_conn: {
                column["name"] for column in inspect(sync_conn).get_columns("workspaces")
            }
        )
        if "logo_url" not in ws_columns:
            await conn.execute(text("ALTER TABLE workspaces ADD COLUMN logo_url VARCHAR DEFAULT ''"))
            migrated = True

    return migrated


async def migrate_otp_security_contract(conn) -> bool:
    """Add failed_attempts column to email_verification_codes table."""
    table_names = await conn.run_sync(
        lambda sync_conn: set(inspect(sync_conn).get_table_names())
    )
    if "email_verification_codes" not in table_names:
        return False
    columns = await conn.run_sync(
        lambda sync_conn: {
            column["name"]
            for column in inspect(sync_conn).get_columns("email_verification_codes")
        }
    )
    if "failed_attempts" not in columns:
        await conn.execute(
            text(
                "ALTER TABLE email_verification_codes "
                "ADD COLUMN failed_attempts INTEGER NOT NULL DEFAULT 0"
            )
        )
        return True
    return False


async def migrate_user_email_verified_contract(conn) -> bool:
    """Ensure email_verified_at, unconfirmed_email exist and email uniqueness is enforced safely."""
    table_names = await conn.run_sync(
        lambda sync_conn: set(inspect(sync_conn).get_table_names())
    )
    if "users" not in table_names:
        return False

    user_columns = await conn.run_sync(
        lambda sync_conn: {
            column["name"] for column in inspect(sync_conn).get_columns("users")
        }
    )

    migrated = False

    # 1. Add email_verified_at if not present
    if "email_verified_at" not in user_columns:
        await conn.execute(text("ALTER TABLE users ADD COLUMN email_verified_at TIMESTAMPTZ"))
        migrated = True

    # 2. Add unconfirmed_email if not present
    if "unconfirmed_email" not in user_columns:
        await conn.execute(text("ALTER TABLE users ADD COLUMN unconfirmed_email VARCHAR"))
        migrated = True

    # 3. Clean up empty string emails to NULL
    await conn.execute(text("UPDATE users SET email = NULL WHERE email IS NOT NULL AND TRIM(email) = ''"))
    await conn.execute(text("UPDATE users SET email = LOWER(TRIM(email)) WHERE email IS NOT NULL"))

    # 4. Deduplicate any legacy duplicate emails keeping the most recent user with active workspace
    dup_rows = (await conn.execute(
        text("SELECT email, COUNT(*) FROM users WHERE email IS NOT NULL GROUP BY email HAVING COUNT(*) > 1")
    )).fetchall()

    for dup_row in dup_rows:
        dup_email = dup_row[0]
        # Find all user IDs with this email ordered by active_workspace_id IS NOT NULL desc, id desc
        users_with_email = (await conn.execute(
            text(
                "SELECT id FROM users WHERE email = :email "
                "ORDER BY (CASE WHEN active_workspace_id IS NOT NULL THEN 1 ELSE 0 END) DESC, id DESC"
            ),
            {"email": dup_email}
        )).scalars().all()
        # Keep the first user, set others to NULL
        if len(users_with_email) > 1:
            remove_ids = users_with_email[1:]
            for r_id in remove_ids:
                await conn.execute(
                    text("UPDATE users SET email = NULL WHERE id = :uid"),
                    {"uid": r_id}
                )
            migrated = True

    # 5. Create unique index on email
    try:
        await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email)"))
    except Exception as e:
        logger.warning("Could not create ix_users_email unique index: %s", e)

    return migrated


async def migrate_legacy_web_auth_tokens(conn) -> int:
    """Move persistent User.auth_token values into expiring hashed sessions."""
    rows = (
        await conn.execute(
            text("SELECT id, auth_token FROM users WHERE auth_token IS NOT NULL")
        )
    ).mappings().all()
    if not rows:
        return 0

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=settings.WEB_SESSION_TTL_HOURS)
    migrated = 0
    for row in rows:
        raw_token = row["auth_token"]
        if not raw_token:
            continue
        await conn.execute(
            text(
                """
                INSERT INTO web_sessions (
                    id, user_id, token_hash, csrf_hash, user_agent, ip_address,
                    created_at, expires_at, last_seen_at, rotated_at, revoked_at
                ) VALUES (
                    :id, :user_id, :token_hash, :csrf_hash, 'Legacy browser', '',
                    :created_at, :expires_at, :last_seen_at, :rotated_at, NULL
                )
                ON CONFLICT (token_hash) DO NOTHING
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "user_id": row["id"],
                "token_hash": hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
                "csrf_hash": hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
                "created_at": now,
                "expires_at": expires_at,
                "last_seen_at": now,
                "rotated_at": now,
            },
        )
        migrated += 1

    await conn.execute(text("UPDATE users SET auth_token = NULL WHERE auth_token IS NOT NULL"))
    return migrated


async def init_schema():
    # Importing the models registers every table on Base.metadata. This makes
    # database initialization reliable for all independent process entrypoints.
    import database.models  # noqa: F401

    async with engine.begin() as conn:
        if await migrate_users_table_contract(conn):
            logger.info("Migrated legacy telegram_users table to users.")
        await conn.run_sync(Base.metadata.create_all)
        migrated_sessions = await migrate_legacy_web_auth_tokens(conn)
        if migrated_sessions:
            logger.info(
                "Migrated %s persistent browser token(s) to expiring sessions.",
                migrated_sessions,
            )
        if await migrate_otp_security_contract(conn):
            logger.info("Added failed_attempts to email_verification_codes.")
        if await migrate_user_email_verified_contract(conn):
            logger.info("Migrated user verified email and unconfirmed email columns.")
        if await migrate_rule_groups_position(conn):
            logger.info("Added position column to rule_groups.")
        if await migrate_onboarding_contract(conn):
            logger.info("Migrated onboarding profile and workspace contracts.")
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
        rule_workspace_migration = await migrate_rule_workspace_contract(conn)
        if any(rule_workspace_migration.values()):
            logger.warning(
                "Migrated workspace-scoped rule contract: %s",
                rule_workspace_migration,
            )
        audit_ownership_migration = await migrate_audit_event_ownership_contract(conn)
        if any(audit_ownership_migration.values()):
            logger.info(
                "Migrated audit event ownership contract: %s",
                audit_ownership_migration,
            )
        jsonb_migration = await migrate_jsonb_native_contract(conn)
        if jsonb_migration["converted"]:
            logger.info(
                "Converted %s legacy JSONB string value(s) to native structures.",
                jsonb_migration["converted"],
            )
        if jsonb_migration["malformed"]:
            logger.warning(
                "Detected %s malformed or wrong-type structured JSONB value(s).",
                jsonb_migration["malformed"],
            )
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


async def ensure_bootstrap_admin():
    if settings.BOOTSTRAP_ADMIN_USERNAME and settings.BOOTSTRAP_ADMIN_PASSWORD:
        from sqlalchemy import select
        from database.models import User

        async with async_session_maker() as session:
            result = await session.execute(
                select(User).where(
                    User.username.ilike(settings.BOOTSTRAP_ADMIN_USERNAME)
                )
            )
            if not result.scalar_one_or_none():
                session.add(
                    User(
                        telegram_id=settings.ADMIN_CHAT_ID or None,
                        username=settings.BOOTSTRAP_ADMIN_USERNAME,
                        full_name=settings.BOOTSTRAP_ADMIN_USERNAME,
                        password_hash=hash_password(settings.BOOTSTRAP_ADMIN_PASSWORD),
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
