import unittest

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot.handlers import _can_manage_account, _is_admin_user
from database.db import Base
from database.models import Account, TelegramUser


class TestBotAccessChecks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.sessions = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        async with self.sessions() as session:
            session.add_all([
                TelegramUser(
                    telegram_id="owner",
                    username="owner",
                    role="buyer",
                    is_approved=True,
                ),
                TelegramUser(
                    telegram_id="other",
                    username="other",
                    role="buyer",
                    is_approved=True,
                ),
                TelegramUser(
                    telegram_id="admin-test",
                    username="admin-test",
                    role="admin",
                    is_approved=True,
                ),
            ])
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_owner_and_admin_can_manage_but_other_buyer_cannot(self):
        account = Account(
            account_id="act_access_test",
            name="Access test",
            access_token="mock",
            owner_id="owner",
        )
        async with self.sessions() as session:
            self.assertTrue(await _can_manage_account(session, "owner", account))
            self.assertTrue(await _can_manage_account(session, "admin-test", account))
            self.assertFalse(await _can_manage_account(session, "other", account))
            self.assertTrue(await _is_admin_user(session, "admin-test"))
            self.assertFalse(await _is_admin_user(session, "other"))
