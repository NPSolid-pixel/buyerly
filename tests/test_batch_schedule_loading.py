import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from cryptography.fernet import Fernet
from database.db import Base
from database.models import Account, AppSettings, AutomationScheduleState, MetaConnection, User
from core.config import settings
from core.meta_tokens import encrypt_meta_token, resolve_account_access_token
from scheduler.worker import MonitoringWorker


from tests.test_db_helper import create_test_engine, init_test_db


class TestBatchScheduleLoading(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_key = settings.META_TOKEN_ENCRYPTION_KEY
        settings.META_TOKEN_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")
        self.engine = create_test_engine()
        self.session_maker = async_sessionmaker(self.engine, expire_on_commit=False)
        await init_test_db(self.engine)

    async def asyncTearDown(self):
        settings.META_TOKEN_ENCRYPTION_KEY = self.original_key
        await self.engine.dispose()

    async def test_get_or_create_schedule_state_cache(self):
        async with self.session_maker() as session:
            acc = Account(account_id="act_test_1", name="Test 1", owner_id="123", is_active=True)
            session.add(acc)
            await session.commit()

            cache = {}
            # 1. First call creates and caches
            state1 = await MonitoringWorker._get_or_create_schedule_state(
                session,
                state_key="rule:act_test_1:index-0",
                account=acc,
                rule_key="index-0",
                state_cache=cache,
            )
            self.assertIn("rule:act_test_1:index-0", cache)
            self.assertEqual(state1.state_key, "rule:act_test_1:index-0")

            # 2. Second call returns from cache directly
            state2 = await MonitoringWorker._get_or_create_schedule_state(
                session,
                state_key="rule:act_test_1:index-0",
                account=acc,
                rule_key="index-0",
                state_cache=cache,
            )
            self.assertIs(state1, state2)

    async def test_resolve_account_access_token_cache(self):
        async with self.session_maker() as session:
            user = User(id=1, telegram_id="123", username="testuser", full_name="Test")
            session.add(user)
            await session.flush()

            conn = MetaConnection(
                id=10,
                owner_id="123",
                owner_user_id=1,
                provider_user_id="fb_123",
                access_token_encrypted=encrypt_meta_token("EAAB_test_token_xyz"),
                status="active",
            )
            session.add(conn)
            await session.flush()

            acc = Account(
                account_id="act_cached_1",
                name="Cached Acc",
                owner_id="123",
                owner_user_id=1,
                meta_connection_id=10,
                is_active=True,
            )
            session.add(acc)
            await session.commit()

            cache = {10: conn}
            token = await resolve_account_access_token(session, acc, connection_cache=cache)
            self.assertEqual(token, "EAAB_test_token_xyz")


if __name__ == "__main__":
    unittest.main()
