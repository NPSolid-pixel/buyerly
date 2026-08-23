import json
import unittest

from sqlalchemy import inspect, text

from database.db import (
    migrate_account_profile_contract,
    migrate_account_day_boundary_contract,
    migrate_account_currency_contract,
    migrate_automation_settings_contract,
    migrate_audit_undo_contract,
    migrate_legacy_account_rules,
    migrate_rule_groups_position,
    migrate_rule_metric_contract,
    migrate_rule_safety_contract,
    migrate_stable_owner_contract,
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


class TestAlembicMigrations(unittest.IsolatedAsyncioTestCase):
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
            self.assertEqual(version, "0001_initial_schema")

            command.downgrade(alembic_cfg, "base")
        finally:
            await init_test_db(engine)
            await engine.dispose()


if __name__ == "__main__":
    unittest.main()
