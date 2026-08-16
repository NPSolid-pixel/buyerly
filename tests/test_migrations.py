import json
import unittest

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from database.db import migrate_legacy_account_rules


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


if __name__ == "__main__":
    unittest.main()
