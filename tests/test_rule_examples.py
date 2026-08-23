import unittest

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.rule_examples import EXAMPLE_GROUPS, EXAMPLE_PRESETS, ensure_rule_examples
from database.db import Base
from database.models import (
    Account,
    RuleExamplesBootstrap,
    RuleGroup,
    RuleGroupItem,
    RulePreset,
    User,
)


from tests.test_db_helper import create_test_engine, init_test_db


class TestRuleExamples(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_test_engine()
        self.sessions = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        await init_test_db(self.engine)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_examples_are_owned_unassigned_and_created_only_once(self):
        async with self.sessions() as session:
            user = User(
                telegram_id="111",
                username="example_owner",
                is_approved=True,
            )
            account = Account(
                account_id="act_examples",
                name="Examples account",
                access_token="token",
                currency="USD",
                rules_enabled=False,
            )
            session.add_all([user, account])
            await session.commit()
            await session.refresh(user)

            self.assertTrue(await ensure_rule_examples(session, user))
            presets = (
                await session.execute(
                    select(RulePreset)
                    .where(RulePreset.owner_user_id == user.id)
                    .order_by(RulePreset.id)
                )
            ).scalars().all()
            groups = (
                await session.execute(
                    select(RuleGroup).where(RuleGroup.owner_user_id == user.id)
                )
            ).scalars().all()
            group_items_count = (
                await session.execute(select(func.count()).select_from(RuleGroupItem))
            ).scalar_one()
            await session.refresh(account)

            self.assertEqual(len(presets), len(EXAMPLE_PRESETS))
            self.assertEqual(len(groups), len(EXAMPLE_GROUPS))
            self.assertEqual(
                group_items_count,
                sum(len(group["preset_keys"]) for group in EXAMPLE_GROUPS),
            )
            self.assertTrue(all(preset.owner_user_id == user.id for preset in presets))
            self.assertFalse(account.rules_enabled)
            self.assertEqual(account.active_rules, "[]")

            deleted_preset_id = presets[0].id
            deleted_group_id = groups[0].id
            await session.execute(
                delete(RuleGroupItem).where(
                    (RuleGroupItem.preset_id == deleted_preset_id)
                    | (RuleGroupItem.group_id == deleted_group_id)
                )
            )
            await session.execute(delete(RulePreset).where(RulePreset.id == deleted_preset_id))
            await session.execute(delete(RuleGroup).where(RuleGroup.id == deleted_group_id))
            await session.commit()

            self.assertFalse(await ensure_rule_examples(session, user))
            remaining_presets = (
                await session.execute(
                    select(func.count())
                    .select_from(RulePreset)
                    .where(RulePreset.owner_user_id == user.id)
                )
            ).scalar_one()
            remaining_groups = (
                await session.execute(
                    select(func.count())
                    .select_from(RuleGroup)
                    .where(RuleGroup.owner_user_id == user.id)
                )
            ).scalar_one()
            marker_count = (
                await session.execute(
                    select(func.count())
                    .select_from(RuleExamplesBootstrap)
                    .where(RuleExamplesBootstrap.owner_user_id == user.id)
                )
            ).scalar_one()

            self.assertEqual(remaining_presets, len(EXAMPLE_PRESETS) - 1)
            self.assertEqual(remaining_groups, len(EXAMPLE_GROUPS) - 1)
            self.assertEqual(marker_count, 1)

    async def test_each_user_receives_an_isolated_library(self):
        async with self.sessions() as session:
            first = User(telegram_id="201", username="first", is_approved=True)
            second = User(telegram_id="202", username="second", is_approved=True)
            session.add_all([first, second])
            await session.commit()
            await session.refresh(first)
            await session.refresh(second)

            await ensure_rule_examples(session, first)
            await ensure_rule_examples(session, second)

            counts = {}
            for user in (first, second):
                counts[user.id] = (
                    await session.execute(
                        select(func.count())
                        .select_from(RulePreset)
                        .where(RulePreset.owner_user_id == user.id)
                    )
                ).scalar_one()
            self.assertEqual(counts[first.id], len(EXAMPLE_PRESETS))
            self.assertEqual(counts[second.id], len(EXAMPLE_PRESETS))
