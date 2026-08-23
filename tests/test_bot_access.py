import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import bot.handlers as bot_handlers
from bot.handlers import _can_manage_account, _is_admin_user, check_user_access
from core.config import settings
from database.db import Base
from database.models import Account, User


from tests.test_db_helper import create_test_engine, init_test_db


class TestBotAccessChecks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_session_maker = bot_handlers.async_session_maker
        self.original_admin_chat_id = settings.ADMIN_CHAT_ID
        self.engine = create_test_engine()
        self.sessions = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        await init_test_db(self.engine)

        async with self.sessions() as session:
            session.add_all([
                User(
                    telegram_id="owner",
                    username="owner",
                    role="buyer",
                    is_approved=True,
                ),
                User(
                    telegram_id="other",
                    username="other",
                    role="buyer",
                    is_approved=True,
                ),
                User(
                    telegram_id="admin-test",
                    username="admin-test",
                    role="admin",
                    is_approved=True,
                ),
            ])
            await session.commit()
        bot_handlers.async_session_maker = self.sessions

    async def asyncTearDown(self):
        bot_handlers.async_session_maker = self.original_session_maker
        settings.ADMIN_CHAT_ID = self.original_admin_chat_id
        await self.engine.dispose()

    async def test_owner_and_admin_can_manage_but_other_buyer_cannot(self):
        async with self.sessions() as session:
            owner = (await session.execute(select(User).where(User.telegram_id == "owner"))).scalar_one()
            account = Account(
                account_id="act_access_test",
                name="Access test",
                access_token="mock",
                owner_user_id=owner.id,
                workspace_id=owner.active_workspace_id,
            )
            session.add(account)
            await session.commit()
            self.assertTrue(await _can_manage_account(session, "owner", account))
            self.assertTrue(await _can_manage_account(session, "admin-test", account))
            self.assertFalse(await _can_manage_account(session, "other", account))
            self.assertTrue(await _is_admin_user(session, "admin-test"))
            self.assertFalse(await _is_admin_user(session, "other"))

    async def test_empty_admin_setting_never_promotes_first_new_user(self):
        settings.ADMIN_CHAT_ID = ""
        message = SimpleNamespace(
            from_user=SimpleNamespace(
                id=123456789,
                username="new_buyer",
                full_name="New Buyer",
            ),
            answer=AsyncMock(),
        )
        bot = SimpleNamespace(send_message=AsyncMock())

        allowed = await check_user_access(message, bot)

        self.assertFalse(allowed)
        self.assertEqual(settings.ADMIN_CHAT_ID, "")
        async with self.sessions() as session:
            result = await session.execute(
                select(User).where(
                    User.telegram_id == "123456789"
                )
            )
            created_user = result.scalar_one()

        self.assertEqual(created_user.role, "buyer")
        self.assertFalse(created_user.is_approved)
        bot.send_message.assert_awaited_once()
        self.assertEqual(
            bot.send_message.await_args.kwargs["chat_id"],
            "admin-test",
        )
