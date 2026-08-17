import json
import sqlite3
import unittest
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from database.db import (
    migrate_legacy_account_rules,
    migrate_rule_metric_contract,
    migrate_rule_safety_contract,
)
from database.migrate_sqlite import _source_rows
from database.models import Account


class TestLegacyAccountRulesMigration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    CREATE TABLE accounts (
                        id INTEGER PRIMARY KEY,
                        preset_id INTEGER,
                        preset_name VARCHAR DEFAULT '',
                        rule_action VARCHAR DEFAULT 'turn_off',
                        rule_conditions TEXT DEFAULT '[]',
                        rule_condition_logic VARCHAR DEFAULT 'and',
                        rule_cooldown_minutes INTEGER DEFAULT 0,
                        rule_check_interval INTEGER DEFAULT 5,
                        rule_notify_tg BOOLEAN DEFAULT 1,
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
                        1,
                        20.0,
                        250.0
                    )
                    """
                ),
                {
                    "conditions": json.dumps(
                        [
                            {
                                "metric": "cpl",
                                "operator": "lte",
                                "value": 5.0,
                                "time_window": "today",
                            }
                        ]
                    )
                },
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO accounts (id, preset_id)
                    VALUES (2, NULL)
                    """
                )
            )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_adds_active_rules_and_preserves_legacy_rule(self):
        async with self.engine.begin() as conn:
            migrated_count = await migrate_legacy_account_rules(conn)
            columns = {
                row[1]
                for row in (
                    await conn.execute(text("PRAGMA table_info(accounts)"))
                ).all()
            }
            rows = (
                await conn.execute(
                    text("SELECT id, active_rules FROM accounts ORDER BY id")
                )
            ).mappings().all()

        self.assertEqual(migrated_count, 1)
        self.assertIn("active_rules", columns)

        migrated_rules = json.loads(rows[0]["active_rules"])
        self.assertEqual(len(migrated_rules), 1)
        self.assertEqual(migrated_rules[0]["preset_id"], 42)
        self.assertEqual(migrated_rules[0]["action"], "increase_budget")
        self.assertEqual(migrated_rules[0]["logic"], "or")
        self.assertEqual(migrated_rules[0]["budget_change_percent"], 20.0)
        self.assertEqual(migrated_rules[0]["check_interval"], 15)
        self.assertEqual(json.loads(rows[1]["active_rules"]), [])

    async def test_is_idempotent(self):
        async with self.engine.begin() as conn:
            first_count = await migrate_legacy_account_rules(conn)
            first_value = (
                await conn.execute(
                    text("SELECT active_rules FROM accounts WHERE id = 1")
                )
            ).scalar_one()
            second_count = await migrate_legacy_account_rules(conn)
            second_value = (
                await conn.execute(
                    text("SELECT active_rules FROM accounts WHERE id = 1")
                )
            ).scalar_one()

        self.assertEqual(first_count, 1)
        self.assertEqual(second_count, 0)
        self.assertEqual(second_value, first_value)


class TestSQLiteToPostgresConversion(unittest.TestCase):
    def test_converts_boolean_and_datetime_values_for_asyncpg(self):
        source = sqlite3.connect(":memory:")
        try:
            source.execute(
                "CREATE TABLE accounts (id INTEGER, rules_enabled BOOLEAN, created_at DATETIME)"
            )
            source.execute(
                "INSERT INTO accounts VALUES (1, 1, '2026-08-17 12:30:00+00:00')"
            )
            rows = _source_rows(source, Account.__table__)
        finally:
            source.close()

        self.assertEqual(rows[0]["rules_enabled"], True)
        self.assertIsInstance(rows[0]["created_at"], datetime)
        self.assertIsNone(rows[0]["created_at"].tzinfo)


class TestRuleMetricContractMigration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE rule_presets (id INTEGER PRIMARY KEY, conditions TEXT NOT NULL)"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE accounts (id INTEGER PRIMARY KEY, active_rules TEXT NOT NULL)"
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
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.execute(
                text("CREATE TABLE accounts (id INTEGER PRIMARY KEY, active_rules TEXT NOT NULL)")
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


if __name__ == "__main__":
    unittest.main()
