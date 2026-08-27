import json
import unittest

from cryptography.fernet import Fernet
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from database.db import (
    migrate_account_profile_contract,
    migrate_account_day_boundary_contract,
    migrate_account_currency_contract,
    migrate_automation_settings_contract,
    migrate_audit_event_ownership_contract,
    migrate_audit_undo_contract,
    migrate_jsonb_native_contract,
    migrate_legacy_account_rules,
    migrate_rule_groups_position,
    migrate_rule_metric_contract,
    migrate_rule_safety_contract,
    migrate_rule_workspace_contract,
    migrate_stable_owner_contract,
)
from database.rule_workspace_contract import (
    WORKSPACE_REVIEW_REASON,
    scope_runtime_rule_snapshots,
)
from core.config import settings
from core.meta_tokens import decrypt_meta_token
from database.models import (
    Account,
    AccountGroup,
    RuleGroup,
    RuleGroupItem,
    RulePreset,
    User,
    Workspace,
    WorkspaceMember,
)
from tests.test_db_helper import create_test_engine, init_test_db


class TestLegacyAccountRulesMigration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_test_engine()
        async with self.engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS accounts CASCADE"))
            await conn.execute(
                text(
                    """
                    CREATE TABLE accounts (
                        id SERIAL PRIMARY KEY,
                        preset_id INTEGER,
                        preset_name VARCHAR DEFAULT '',
                        rule_action VARCHAR DEFAULT 'turn_off',
                        rule_conditions TEXT DEFAULT '[]',
                        rule_condition_logic VARCHAR DEFAULT 'and',
                        rule_cooldown_minutes INTEGER DEFAULT 0,
                        rule_check_interval INTEGER DEFAULT 5,
                        rule_notify_tg BOOLEAN DEFAULT TRUE,
                        rule_budget_change_percent FLOAT DEFAULT 0.0,
                        rule_budget_max_daily FLOAT DEFAULT 0.0
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO accounts (
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
                    ) VALUES (
                        1,
                        42,
                        'Legacy rule',
                        'increase_budget',
                        :conditions,
                        'or',
                        30,
                        15,
                        TRUE,
                        15.0,
                        150.0
                    )
                    """
                ),
                {
                    "conditions": json.dumps(
                        [
                            {
                                "metric": "spend",
                                "operator": "gte",
                                "value": 10.0,
                                "time_window": "today",
                            }
                        ]
                    )
                },
            )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_legacy_account_rules_are_migrated_idempotently(self):
        async with self.engine.begin() as conn:
            first_count = await migrate_legacy_account_rules(conn)
            first_value = json.loads(
                (
                    await conn.execute(
                        text("SELECT active_rules FROM accounts WHERE id = 1")
                    )
                ).scalar_one()
            )
            columns = await conn.run_sync(
                lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("accounts")}
            )

        self.assertIn("active_rules", columns)
        self.assertEqual(
            first_value,
            [
                {
                    "preset_id": 42,
                    "name": "Legacy rule",
                    "action": "increase_budget",
                    "conditions": [
                        {
                            "metric": "spend",
                            "operator": "gte",
                            "value": 10.0,
                            "time_window": "today",
                        }
                    ],
                    "logic": "or",
                    "cooldown_minutes": 30,
                    "check_interval": 15,
                    "notify_tg": True,
                    "budget_change_percent": 15.0,
                    "budget_max_daily": 150.0,
                }
            ],
        )

        async with self.engine.begin() as conn:
            second_count = await migrate_legacy_account_rules(conn)
            second_value = json.loads(
                (
                    await conn.execute(
                        text("SELECT active_rules FROM accounts WHERE id = 1")
                    )
                ).scalar_one()
            )

        self.assertEqual(first_count, 1)
        self.assertEqual(second_count, 0)
        self.assertEqual(second_value, first_value)


class TestAccountProfileContractMigration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_test_engine()
        async with self.engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS accounts CASCADE"))
            await conn.execute(
                text(
                    "CREATE TABLE accounts ("
                    "id SERIAL PRIMARY KEY, name VARCHAR NOT NULL)"
                )
            )
            await conn.execute(
                text("INSERT INTO accounts (id, name) VALUES (1, 'Meta name')")
            )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_adds_editable_fields_without_rewriting_existing_name(self):
        async with self.engine.begin() as conn:
            first_changed = await migrate_account_profile_contract(conn)
            second_changed = await migrate_account_profile_contract(conn)
            columns = await conn.run_sync(
                lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("accounts")}
            )
            row = (
                await conn.execute(
                    text("SELECT name, custom_name, note FROM accounts WHERE id = 1")
                )
            ).mappings().one()

        self.assertTrue(first_changed)
        self.assertFalse(second_changed)
        self.assertIn("custom_name", columns)
        self.assertIn("note", columns)
        self.assertEqual(row["name"], "Meta name")
        self.assertEqual(row["custom_name"], "")
        self.assertEqual(row["note"], "")


class TestRuleMetricContractMigration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_test_engine()
        async with self.engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS rule_presets CASCADE"))
            await conn.execute(text("DROP TABLE IF EXISTS accounts CASCADE"))
            await conn.execute(
                text(
                    "CREATE TABLE rule_presets (id SERIAL PRIMARY KEY, conditions TEXT NOT NULL)"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE accounts (id SERIAL PRIMARY KEY, active_rules TEXT NOT NULL)"
                )
            )
            await conn.execute(
                text("INSERT INTO rule_presets (id, conditions) VALUES (1, :conditions)"),
                {
                    "conditions": json.dumps(
                        [
                            {"metric": "cpr", "operator": "gte", "value": 5},
                            {"metric": "cpa", "operator": "lte", "value": 10},
                        ]
                    )
                },
            )
            await conn.execute(
                text("INSERT INTO accounts (id, active_rules) VALUES (1, :rules)"),
                {
                    "rules": json.dumps(
                        [
                            {
                                "preset_id": 1,
                                "conditions": [
                                    {"metric": "cpr", "operator": "gte", "value": 5}
                                ],
                            },
                            {
                                "preset_id": 2,
                                "conditions": [
                                    {"metric": "cpa", "operator": "lte", "value": 10}
                                ],
                            },
                        ]
                    )
                },
            )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_normalizes_cpreg_and_disables_combined_cpa_rule(self):
        async with self.engine.begin() as conn:
            first = await migrate_rule_metric_contract(conn)
            preset_conditions = json.loads(
                (
                    await conn.execute(
                        text("SELECT conditions FROM rule_presets WHERE id = 1")
                    )
                ).scalar_one()
            )
            account_rules = json.loads(
                (
                    await conn.execute(
                        text("SELECT active_rules FROM accounts WHERE id = 1")
                    )
                ).scalar_one()
            )
            second = await migrate_rule_metric_contract(conn)

        self.assertEqual(first["presets_updated"], 1)
        self.assertEqual(first["account_rules_updated"], 1)
        self.assertEqual(first["rules_disabled"], 1)
        self.assertEqual(preset_conditions[0]["metric"], "cpreg")
        self.assertEqual(preset_conditions[1]["metric"], "legacy_cpa")
        self.assertEqual(account_rules[0]["conditions"][0]["metric"], "cpreg")
        self.assertNotIn("needs_review", account_rules[0])
        self.assertFalse(account_rules[1]["enabled"])
        self.assertTrue(account_rules[1]["needs_review"])
        self.assertEqual(second, {"presets_updated": 0, "account_rules_updated": 0, "rules_disabled": 0})


class TestRuleSafetyContractMigration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_test_engine()
        async with self.engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS accounts CASCADE"))
            await conn.execute(
                text("CREATE TABLE accounts (id SERIAL PRIMARY KEY, active_rules TEXT NOT NULL)")
            )
            await conn.execute(
                text("INSERT INTO accounts (id, active_rules) VALUES (1, :rules)"),
                {
                    "rules": json.dumps(
                        [
                            {
                                "preset_id": 10,
                                "name": "Unsafe legacy action",
                                "action": "destroy",
                                "conditions": [
                                    {"metric": "spend", "operator": "gte", "value": 10, "time_window": "today"}
                                ],
                                "logic": "and",
                                "cooldown_minutes": 0,
                                "check_interval": 5,
                                "budget_change_percent": 0,
                                "budget_max_daily": 0,
                            }
                        ]
                    )
                },
            )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_invalid_runtime_rules_are_disabled_idempotently(self):
        async with self.engine.begin() as conn:
            first = await migrate_rule_safety_contract(conn)
            migrated = json.loads(
                (
                    await conn.execute(text("SELECT active_rules FROM accounts WHERE id = 1"))
                ).scalar_one()
            )
            second = await migrate_rule_safety_contract(conn)

        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        self.assertFalse(migrated[0]["enabled"])
        self.assertTrue(migrated[0]["needs_review"])
        self.assertIn("отключено", migrated[0]["review_reason"])


class TestAuditUndoContractMigration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_test_engine()
        async with self.engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS audit_events CASCADE"))
            await conn.execute(
                text(
                    "CREATE TABLE audit_events ("
                    "id SERIAL PRIMARY KEY, event_type VARCHAR NOT NULL)"
                )
            )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_adds_reversal_link_idempotently(self):
        async with self.engine.begin() as conn:
            first = await migrate_audit_undo_contract(conn)
            second = await migrate_audit_undo_contract(conn)
            columns = await conn.run_sync(
                lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("audit_events")}
            )
            indexes = await conn.run_sync(
                lambda sync_conn: {ix["name"] for ix in inspect(sync_conn).get_indexes("audit_events")}
            )

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertIn("reverts_event_id", columns)
        self.assertIn("ix_audit_events_reverts_event_id", indexes)


class TestAutomationSettingsContractMigration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_test_engine()
        async with self.engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS app_settings CASCADE"))
            await conn.execute(
                text(
                    "CREATE TABLE app_settings ("
                    "id SERIAL PRIMARY KEY, "
                    "poll_interval_minutes INTEGER NOT NULL DEFAULT 10, "
                    "admin_chat_id VARCHAR NOT NULL DEFAULT '')"
                )
            )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_adds_safe_polling_controls_idempotently(self):
        async with self.engine.begin() as conn:
            first = await migrate_automation_settings_contract(conn)
            columns = await conn.run_sync(
                lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("app_settings")}
            )
            second = await migrate_automation_settings_contract(conn)

        self.assertEqual(
            set(first),
            {
                "critical_rule_interval_minutes",
                "stop_confirmation_minutes",
                "inventory_cache_minutes",
                "account_health_interval_minutes",
                "max_concurrent_accounts",
                "max_concurrent_actions",
                "usage_soft_limit_percent",
                "usage_hard_limit_percent",
                "adaptive_polling_enabled",
                "updated_at",
            },
        )
        self.assertTrue(set(first).issubset(columns))
        self.assertEqual(second, [])


class TestUsersTableMigration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_test_engine()
        async with self.engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS users CASCADE"))
            await conn.execute(text("DROP TABLE IF EXISTS telegram_users CASCADE"))
            await conn.execute(
                text("CREATE TABLE telegram_users (id SERIAL PRIMARY KEY, username VARCHAR UNIQUE)")
            )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_renames_telegram_users_to_users(self):
        from database.db import migrate_users_table_contract
        async with self.engine.begin() as conn:
            migrated = await migrate_users_table_contract(conn)
            second = await migrate_users_table_contract(conn)
            tables = await conn.run_sync(lambda sync_conn: set(inspect(sync_conn).get_table_names()))
        self.assertTrue(migrated)
        self.assertFalse(second)
        self.assertIn("users", tables)
        self.assertNotIn("telegram_users", tables)


class TestStableOwnerContractMigration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_test_engine()
        async with self.engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS accounts CASCADE"))
            await conn.execute(text("DROP TABLE IF EXISTS users CASCADE"))
            await conn.execute(
                text(
                    "CREATE TABLE users ("
                    "id SERIAL PRIMARY KEY, telegram_id VARCHAR UNIQUE)"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE accounts ("
                    "id SERIAL PRIMARY KEY, owner_id VARCHAR NOT NULL)"
                )
            )
            await conn.execute(
                text("INSERT INTO users (id, telegram_id) VALUES (7, 'tg-old')")
            )
            await conn.execute(
                text("INSERT INTO accounts (id, owner_id) VALUES (1, 'tg-old')")
            )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_backfills_internal_user_id_without_rewriting_legacy_owner(self):
        async with self.engine.begin() as conn:
            first = await migrate_stable_owner_contract(conn)
            second = await migrate_stable_owner_contract(conn)
            row = (
                await conn.execute(
                    text("SELECT owner_id, owner_user_id FROM accounts WHERE id = 1")
                )
            ).one()

        self.assertEqual(row, ("tg-old", 7))
        self.assertEqual(first["accounts"], 1)
        self.assertEqual(second["accounts"], 0)


class TestAccountCurrencyContractMigration(unittest.IsolatedAsyncioTestCase):
    async def test_adds_unknown_currency_without_guessing_usd(self):
        engine = create_test_engine()
        try:
            async with engine.begin() as conn:
                await conn.execute(text("DROP TABLE IF EXISTS accounts CASCADE"))
                await conn.execute(
                    text("CREATE TABLE accounts (id SERIAL PRIMARY KEY, name VARCHAR)")
                )
                await conn.execute(
                    text("INSERT INTO accounts (id, name) VALUES (1, 'Legacy')")
                )
                first = await migrate_account_currency_contract(conn)
                second = await migrate_account_currency_contract(conn)
                currency = (
                    await conn.execute(text("SELECT currency FROM accounts WHERE id = 1"))
                ).scalar_one()
            self.assertTrue(first)
            self.assertFalse(second)
            self.assertEqual(currency, "UNKNOWN")
        finally:
            await engine.dispose()


class TestAccountDayBoundaryContractMigration(unittest.IsolatedAsyncioTestCase):
    async def test_adds_independent_empty_day_marker_idempotently(self):
        engine = create_test_engine()
        try:
            async with engine.begin() as conn:
                await conn.execute(text("DROP TABLE IF EXISTS accounts CASCADE"))
                await conn.execute(
                    text(
                        "CREATE TABLE accounts ("
                        "id SERIAL PRIMARY KEY, last_started_date VARCHAR NOT NULL DEFAULT '')"
                    )
                )
                await conn.execute(
                    text("INSERT INTO accounts (id, last_started_date) VALUES (1, '2026-08-17')")
                )
                first = await migrate_account_day_boundary_contract(conn)
                second = await migrate_account_day_boundary_contract(conn)
                row = (
                    await conn.execute(
                        text(
                            "SELECT last_started_date, last_day_start_date "
                            "FROM accounts WHERE id = 1"
                        )
                    )
                ).one()
            self.assertTrue(first)
            self.assertFalse(second)
            self.assertEqual(row, ("2026-08-17", ""))
        finally:
            await engine.dispose()


class TestRuleGroupsPositionMigration(unittest.IsolatedAsyncioTestCase):
    async def test_adds_position_column_idempotently(self):
        engine = create_test_engine()
        try:
            async with engine.begin() as conn:
                await conn.execute(text("DROP TABLE IF EXISTS rule_groups CASCADE"))
                await conn.execute(
                    text(
                        "CREATE TABLE rule_groups ("
                        "id SERIAL PRIMARY KEY, name VARCHAR NOT NULL, description VARCHAR DEFAULT '')"
                    )
                )
                await conn.execute(
                    text("INSERT INTO rule_groups (id, name) VALUES (1, 'Test Group')")
                )
                first = await migrate_rule_groups_position(conn)
                second = await migrate_rule_groups_position(conn)
                row = (
                    await conn.execute(
                        text("SELECT id, name, position FROM rule_groups WHERE id = 1")
                    )
                ).one()
            self.assertTrue(first)
            self.assertFalse(second)
            self.assertEqual(row, (1, "Test Group", 0))
        finally:
            await engine.dispose()


class TestAuditEventOwnershipContractMigration(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_owner_constraint_is_removed_and_scope_is_backfilled(self):
        engine = create_test_engine()
        try:
            async with engine.begin() as conn:
                await conn.execute(text("DROP TABLE IF EXISTS audit_events CASCADE"))
                await conn.execute(text("DROP TABLE IF EXISTS accounts CASCADE"))
                await conn.execute(
                    text(
                        "CREATE TABLE accounts ("
                        "id SERIAL PRIMARY KEY, "
                        "account_id VARCHAR UNIQUE NOT NULL, "
                        "owner_user_id INTEGER, "
                        "workspace_id INTEGER)"
                    )
                )
                await conn.execute(
                    text(
                        "CREATE TABLE audit_events ("
                        "id SERIAL PRIMARY KEY, "
                        "owner_id VARCHAR NOT NULL, "
                        "owner_user_id INTEGER, "
                        "workspace_id INTEGER, "
                        "account_id VARCHAR NOT NULL, "
                        "event_type VARCHAR NOT NULL)"
                    )
                )
                await conn.execute(
                    text(
                        "INSERT INTO accounts "
                        "(account_id, owner_user_id, workspace_id) "
                        "VALUES ('act-worker', 17, 23)"
                    )
                )
                await conn.execute(
                    text(
                        "INSERT INTO audit_events "
                        "(owner_id, account_id, event_type) "
                        "VALUES ('legacy-owner', 'act-worker', 'ACCOUNT_DAY_STARTED')"
                    )
                )

                first = await migrate_audit_event_ownership_contract(conn)
                second = await migrate_audit_event_ownership_contract(conn)
                owner_column = await conn.run_sync(
                    lambda sync_conn: next(
                        column
                        for column in inspect(sync_conn).get_columns("audit_events")
                        if column["name"] == "owner_id"
                    )
                )
                migrated_row = (
                    await conn.execute(
                        text(
                            "SELECT owner_user_id, workspace_id "
                            "FROM audit_events WHERE account_id = 'act-worker'"
                        )
                    )
                ).one()
                await conn.execute(
                    text(
                        "INSERT INTO audit_events "
                        "(owner_user_id, workspace_id, account_id, event_type) "
                        "VALUES (17, 23, 'act-worker', 'TOKEN_EXPIRED')"
                    )
                )

            self.assertTrue(first["legacy_owner_constraint_removed"])
            self.assertEqual(first["owners_backfilled"], 1)
            self.assertEqual(first["workspaces_backfilled"], 1)
            self.assertEqual(
                second,
                {
                    "legacy_owner_constraint_removed": False,
                    "owners_backfilled": 0,
                    "workspaces_backfilled": 0,
                },
            )
            self.assertTrue(owner_column["nullable"])
            self.assertEqual(migrated_row, (17, 23))
        finally:
            await init_test_db(engine)
            await engine.dispose()


class TestNativeJsonbMigration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_test_engine()
        await init_test_db(self.engine)
        async with self.engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS rule_presets CASCADE"))
            await conn.execute(text("DROP TABLE IF EXISTS automation_runtime_states CASCADE"))
            await conn.execute(
                text(
                    "CREATE TABLE rule_presets ("
                    "id INTEGER PRIMARY KEY, conditions JSONB NOT NULL)"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE automation_runtime_states ("
                    "state_key VARCHAR PRIMARY KEY, payload JSONB NOT NULL)"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO rule_presets (id, conditions) VALUES "
                    "(1, to_jsonb(CAST(:legacy_array AS TEXT))), "
                    "(2, CAST(:native_array AS JSONB)), "
                    "(3, to_jsonb(CAST(:malformed AS TEXT)))"
                ),
                {
                    "legacy_array": '[{"metric":"spend"}]',
                    "native_array": '[{"metric":"cpl"}]',
                    "malformed": "not-json",
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO automation_runtime_states (state_key, payload) "
                    "VALUES ('monitoring', to_jsonb(CAST(:legacy_object AS TEXT)))"
                ),
                {"legacy_object": '{"cycle_id":"cycle-1"}'},
            )

    async def asyncTearDown(self):
        await init_test_db(self.engine)
        await self.engine.dispose()

    async def test_converts_valid_strings_and_reports_bad_values_idempotently(self):
        async with self.engine.begin() as conn:
            first = await migrate_jsonb_native_contract(conn)
            rows = (
                await conn.execute(
                    text(
                        "SELECT id, jsonb_typeof(conditions) AS kind, conditions "
                        "FROM rule_presets ORDER BY id"
                    )
                )
            ).mappings().all()
            runtime = (
                await conn.execute(
                    text(
                        "SELECT jsonb_typeof(payload) AS kind, payload "
                        "FROM automation_runtime_states WHERE state_key = 'monitoring'"
                    )
                )
            ).mappings().one()
            second = await migrate_jsonb_native_contract(conn)

        self.assertEqual(first, {"converted": 2, "malformed": 1})
        self.assertEqual(rows[0]["kind"], "array")
        self.assertEqual(rows[0]["conditions"], [{"metric": "spend"}])
        self.assertEqual(rows[1]["kind"], "array")
        self.assertEqual(rows[2]["kind"], "string")
        self.assertEqual(runtime["kind"], "object")
        self.assertEqual(runtime["payload"], {"cycle_id": "cycle-1"})
        self.assertEqual(second, {"converted": 0, "malformed": 1})


class TestRuleWorkspaceMigration(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_helper_stamps_safe_rules_and_disables_unsafe_rules(self):
        rules, changed, disabled = scope_runtime_rule_snapshots(
            [
                {"preset_id": 10, "name": "safe"},
                {"preset_id": 20, "name": "cross"},
                {"preset_id": 999, "name": "missing"},
                "malformed",
            ],
            account_workspace_id=1,
            preset_workspaces={10: 1, 20: 2},
        )

        self.assertTrue(changed)
        self.assertEqual(disabled, 2)
        self.assertEqual(rules[0]["workspace_id"], 1)
        self.assertNotIn("needs_review", rules[0])
        self.assertFalse(rules[1]["enabled"])
        self.assertTrue(rules[1]["needs_review"])
        self.assertEqual(rules[1]["review_reason"], WORKSPACE_REVIEW_REASON)
        self.assertFalse(rules[2]["enabled"])
        self.assertEqual(len(rules), 3)

    async def test_runtime_migration_backfills_and_enforces_group_workspace(self):
        engine = create_test_engine()
        sessions = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        try:
            await init_test_db(engine)
            async with sessions() as session:
                owner = User(username="rule-migration-owner", is_approved=True)
                session.add(owner)
                await session.flush()
                first_workspace = Workspace(
                    name="First workspace",
                    slug="rule-migration-first",
                    owner_user_id=owner.id,
                )
                second_workspace = Workspace(
                    name="Second workspace",
                    slug="rule-migration-second",
                    owner_user_id=owner.id,
                )
                session.add_all([first_workspace, second_workspace])
                await session.flush()
                session.add_all(
                    [
                        WorkspaceMember(
                            workspace_id=first_workspace.id,
                            user_id=owner.id,
                            role="owner",
                        ),
                        WorkspaceMember(
                            workspace_id=second_workspace.id,
                            user_id=owner.id,
                            role="owner",
                        ),
                    ]
                )
                owner.active_workspace_id = first_workspace.id
                safe_preset = RulePreset(
                    workspace_id=None,
                    owner_user_id=owner.id,
                    name="Legacy safe",
                    action="turn_off",
                    conditions=[],
                )
                cross_preset = RulePreset(
                    workspace_id=second_workspace.id,
                    owner_user_id=owner.id,
                    name="Cross workspace",
                    action="turn_off",
                    conditions=[],
                )
                group = RuleGroup(
                    workspace_id=first_workspace.id,
                    owner_user_id=owner.id,
                    name="First group",
                    position=9,
                )
                session.add_all([safe_preset, cross_preset, group])
                await session.flush()
                session.add(
                    RuleGroupItem(
                        group_id=group.id,
                        preset_id=cross_preset.id,
                        position=0,
                    )
                )
                session.add(
                    Account(
                        account_id="act_rule_migration",
                        name="Migration account",
                        workspace_id=first_workspace.id,
                        owner_user_id=owner.id,
                        currency="USD",
                        rules_enabled=True,
                        active_rules=json.dumps(
                            [
                                {"preset_id": safe_preset.id, "name": "safe"},
                                {"preset_id": cross_preset.id, "name": "cross"},
                            ]
                        ),
                    )
                )
                await session.commit()
                safe_preset_id = safe_preset.id
                cross_preset_id = cross_preset.id
                group_id = group.id
                workspace_id = first_workspace.id

            async with engine.begin() as conn:
                first = await migrate_rule_workspace_contract(conn)
                second = await migrate_rule_workspace_contract(conn)
                stored_workspace = (
                    await conn.execute(
                        text("SELECT workspace_id FROM rule_presets WHERE id = :id"),
                        {"id": safe_preset_id},
                    )
                ).scalar_one()
                stored_rules = json.loads(
                    (
                        await conn.execute(
                            text(
                                "SELECT active_rules FROM accounts "
                                "WHERE account_id = 'act_rule_migration'"
                            )
                        )
                    ).scalar_one()
                )
                link_count = (
                    await conn.execute(
                        text(
                            "SELECT COUNT(*) FROM rule_group_items "
                            "WHERE group_id = :group_id"
                        ),
                        {"group_id": group_id},
                    )
                ).scalar_one()

            self.assertEqual(stored_workspace, workspace_id)
            self.assertEqual(first["presets_backfilled"], 1)
            self.assertEqual(first["group_links_removed"], 1)
            self.assertEqual(first["snapshots_changed"], 1)
            self.assertEqual(first["snapshots_disabled"], 1)
            self.assertFalse(any(second.values()))
            self.assertEqual(stored_rules[0]["workspace_id"], workspace_id)
            self.assertTrue(stored_rules[1]["needs_review"])
            self.assertEqual(link_count, 0)

            with self.assertRaises(Exception):
                async with engine.begin() as conn:
                    await conn.execute(
                        text(
                            "INSERT INTO rule_group_items "
                            "(group_id, preset_id, position, created_at) "
                            "VALUES (:group_id, :preset_id, 0, NOW())"
                        ),
                        {"group_id": group_id, "preset_id": cross_preset_id},
                    )
        finally:
            await init_test_db(engine)
            await engine.dispose()


class TestAlembicMigrations(unittest.IsolatedAsyncioTestCase):
    async def test_account_group_uniqueness_migration_preserves_and_scopes_names(self):
        from alembic.config import Config
        from alembic import command
        import os

        engine = create_test_engine()
        try:
            async with engine.begin() as conn:
                await conn.execute(text("DROP SCHEMA public CASCADE"))
                await conn.execute(text("CREATE SCHEMA public"))

            config = Config(
                os.path.join(os.path.dirname(os.path.dirname(__file__)), "alembic.ini")
            )
            config.set_main_option(
                "script_location",
                os.path.join(os.path.dirname(os.path.dirname(__file__)), "alembic"),
            )
            command.upgrade(config, "0011_workspace_slugs")

            session_maker = async_sessionmaker(
                engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )
            async with session_maker() as session:
                first_owner = User(username="group-owner-one")
                second_owner = User(username="group-owner-two")
                session.add_all([first_owner, second_owner])
                await session.flush()
                workspace = Workspace(
                    name="Group Workspace",
                    slug="group-workspace",
                    owner_user_id=first_owner.id,
                )
                session.add(workspace)
                await session.flush()
                session.add(
                    WorkspaceMember(
                        workspace_id=workspace.id,
                        user_id=first_owner.id,
                        role="owner",
                    )
                )
                first_owner.active_workspace_id = workspace.id
                session.add_all(
                    [
                        AccountGroup(
                            workspace_id=workspace.id,
                            owner_user_id=first_owner.id,
                            name="  Alpha  ",
                        ),
                        AccountGroup(
                            workspace_id=workspace.id,
                            owner_user_id=second_owner.id,
                            name="alpha",
                        ),
                        AccountGroup(
                            workspace_id=None,
                            owner_user_id=first_owner.id,
                            name="Legacy",
                        ),
                    ]
                )
                await session.commit()

            command.upgrade(config, "head")

            async with engine.connect() as conn:
                rows = (
                    await conn.execute(
                        text(
                            "SELECT workspace_id, name FROM account_groups ORDER BY id ASC"
                        )
                    )
                ).all()
                self.assertEqual(
                    [(row.workspace_id, row.name) for row in rows],
                    [
                        (workspace.id, "Alpha"),
                        (workspace.id, "alpha (2)"),
                        (workspace.id, "Legacy"),
                    ],
                )

            async with session_maker() as session:
                second_workspace = Workspace(
                    name="Second Group Workspace",
                    slug="second-group-workspace",
                    owner_user_id=first_owner.id,
                )
                session.add(second_workspace)
                await session.flush()
                session.add(
                    AccountGroup(
                        workspace_id=second_workspace.id,
                        owner_user_id=first_owner.id,
                        name="Alpha",
                    )
                )
                await session.commit()

            async with session_maker() as session:
                session.add(
                    AccountGroup(
                        workspace_id=workspace.id,
                        owner_user_id=first_owner.id,
                        name="ALPHA",
                    )
                )
                with self.assertRaises(Exception):
                    await session.commit()
                await session.rollback()
        finally:
            await init_test_db(engine)
            await engine.dispose()

    async def test_manual_token_migration_encrypts_plaintext_and_downgrades_safely(self):
        from alembic import command
        from alembic.config import Config
        import os

        original_key = settings.META_TOKEN_ENCRYPTION_KEY
        settings.META_TOKEN_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")
        engine = create_test_engine()
        try:
            async with engine.begin() as conn:
                await conn.execute(text("DROP SCHEMA public CASCADE"))
                await conn.execute(text("CREATE SCHEMA public"))

            config = Config(
                os.path.join(os.path.dirname(os.path.dirname(__file__)), "alembic.ini")
            )
            config.set_main_option(
                "script_location",
                os.path.join(os.path.dirname(os.path.dirname(__file__)), "alembic"),
            )
            command.upgrade(config, "0012_group_ws_unique")

            raw_token = "EAAB-legacy-manual-token"
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        """
                        INSERT INTO accounts (
                            account_id, name, custom_name, note, access_token,
                            batch_name, currency, timezone_name, last_started_date,
                            last_day_start_date, active_rules, account_status,
                            status_label, rules_enabled, is_active, created_at
                        ) VALUES (
                            'act_legacy_manual', 'Legacy manual', '', '', :token,
                            '', 'USD', 'UTC', '', '', '[]', 1,
                            'Активен (ACTIVE)', false, true, NOW()
                        )
                        """
                    ),
                    {"token": raw_token},
                )

            command.upgrade(config, "head")
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        text(
                            "SELECT access_token, access_token_encrypted "
                            "FROM accounts WHERE account_id = 'act_legacy_manual'"
                        )
                    )
                ).one()
                self.assertEqual(row.access_token, "")
                self.assertNotIn(raw_token, row.access_token_encrypted)
                self.assertEqual(
                    decrypt_meta_token(row.access_token_encrypted),
                    raw_token,
                )

            command.downgrade(config, "0012_group_ws_unique")
            async with engine.connect() as conn:
                restored = (
                    await conn.execute(
                        text(
                            "SELECT access_token FROM accounts "
                            "WHERE account_id = 'act_legacy_manual'"
                        )
                    )
                ).scalar_one()
                self.assertEqual(restored, raw_token)
        finally:
            settings.META_TOKEN_ENCRYPTION_KEY = original_key
            await init_test_db(engine)
            await engine.dispose()

    async def test_workspace_slug_migration_normalizes_reserved_and_colliding_rows(self):
        from alembic.config import Config
        from alembic import command
        import os

        engine = create_test_engine()
        try:
            async with engine.begin() as conn:
                await conn.execute(text("DROP SCHEMA public CASCADE"))
                await conn.execute(text("CREATE SCHEMA public"))

            config = Config(
                os.path.join(os.path.dirname(os.path.dirname(__file__)), "alembic.ini")
            )
            config.set_main_option(
                "script_location",
                os.path.join(os.path.dirname(os.path.dirname(__file__)), "alembic"),
            )
            command.upgrade(config, "0010_atomic_otp")

            session_maker = async_sessionmaker(
                engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )
            async with session_maker() as session:
                owner = User(username="slug-migration-owner")
                session.add(owner)
                await session.flush()
                session.add_all(
                    [
                        Workspace(name="API", slug="api", owner_user_id=owner.id),
                        Workspace(
                            name="Канада Трафик",
                            slug="Канада-Трафик",
                            owner_user_id=owner.id,
                        ),
                        Workspace(
                            name="Canada Traffic",
                            slug="kanada-trafik",
                            owner_user_id=owner.id,
                        ),
                    ]
                )
                await session.commit()

            command.upgrade(config, "head")

            async with engine.connect() as conn:
                slugs = list(
                    (
                        await conn.execute(
                            text("SELECT slug FROM workspaces ORDER BY id ASC")
                        )
                    ).scalars()
                )
                self.assertEqual(
                    slugs,
                    ["api-workspace", "kanada-trafik-2", "kanada-trafik"],
                )
        finally:
            await init_test_db(engine)
            await engine.dispose()

    async def test_alembic_upgrade_head_applies_successfully(self):
        from alembic.config import Config
        from alembic import command
        import os

        engine = create_test_engine()
        try:
            async with engine.begin() as conn:
                await conn.execute(text("DROP SCHEMA public CASCADE"))
                await conn.execute(text("CREATE SCHEMA public"))

            ini_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "alembic.ini")
            alembic_cfg = Config(ini_path)
            alembic_cfg.set_main_option(
                "script_location",
                os.path.join(os.path.dirname(os.path.dirname(__file__)), "alembic"),
            )
            command.upgrade(alembic_cfg, "head")

            async with engine.begin() as conn:
                version = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()
                self.assertEqual(version, "0015_meta_oauth_reconnect_id")

            command.downgrade(alembic_cfg, "base")
        finally:
            await init_test_db(engine)
            await engine.dispose()

    async def test_production_migration_runner_is_idempotent_on_fresh_database(self):
        from database.migrations import run_production_migrations

        engine = create_test_engine()
        try:
            async with engine.begin() as conn:
                await conn.execute(text("DROP SCHEMA public CASCADE"))
                await conn.execute(text("CREATE SCHEMA public"))

            await run_production_migrations()
            await run_production_migrations()

            async with engine.connect() as conn:
                version = (
                    await conn.execute(text("SELECT version_num FROM alembic_version"))
                ).scalar_one()
                self.assertEqual(version, "0015_meta_oauth_reconnect_id")
        finally:
            await init_test_db(engine)
            await engine.dispose()

    async def test_production_migration_runner_adopts_valid_legacy_schema(self):
        from database.migrations import run_production_migrations
        from alembic import command
        from alembic.config import Config
        import os

        engine = create_test_engine()
        try:
            async with engine.begin() as conn:
                await conn.execute(text("DROP SCHEMA public CASCADE"))
                await conn.execute(text("CREATE SCHEMA public"))

            config = Config(
                os.path.join(os.path.dirname(os.path.dirname(__file__)), "alembic.ini")
            )
            config.set_main_option(
                "script_location",
                os.path.join(os.path.dirname(os.path.dirname(__file__)), "alembic"),
            )
            command.upgrade(config, "0009_web_sessions")
            async with engine.begin() as conn:
                await conn.execute(text("DROP TABLE alembic_version"))

            await run_production_migrations()

            async with engine.connect() as conn:
                version = (
                    await conn.execute(text("SELECT version_num FROM alembic_version"))
                ).scalar_one()
                self.assertEqual(version, "0015_meta_oauth_reconnect_id")
        finally:
            await init_test_db(engine)
            await engine.dispose()

    async def test_production_migration_runner_repairs_empty_version_table(self):
        from database.migrations import run_production_migrations
        from alembic import command
        from alembic.config import Config
        import os

        engine = create_test_engine()
        try:
            async with engine.begin() as conn:
                await conn.execute(text("DROP SCHEMA public CASCADE"))
                await conn.execute(text("CREATE SCHEMA public"))

            config = Config(
                os.path.join(os.path.dirname(os.path.dirname(__file__)), "alembic.ini")
            )
            config.set_main_option(
                "script_location",
                os.path.join(os.path.dirname(os.path.dirname(__file__)), "alembic"),
            )
            command.upgrade(config, "0009_web_sessions")
            async with engine.begin() as conn:
                await conn.execute(text("DELETE FROM alembic_version"))

            await run_production_migrations()

            async with engine.connect() as conn:
                version = (
                    await conn.execute(text("SELECT version_num FROM alembic_version"))
                ).scalar_one()
                self.assertEqual(version, "0015_meta_oauth_reconnect_id")
        finally:
            await init_test_db(engine)
            await engine.dispose()

    async def test_production_migration_runner_rejects_legacy_schema_drift(self):
        from database.migrations import run_production_migrations

        engine = create_test_engine()
        try:
            await init_test_db(engine)
            async with engine.begin() as conn:
                await conn.execute(text("ALTER TABLE users DROP COLUMN first_name"))

            with self.assertRaisesRegex(RuntimeError, "schema drift"):
                await run_production_migrations()
        finally:
            await init_test_db(engine)
            await engine.dispose()


if __name__ == "__main__":
    unittest.main()
