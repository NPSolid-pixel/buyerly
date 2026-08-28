import unittest

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from database.models import Account, User, Workspace, WorkspaceMember
from services.account_health import classify_health_error, health_payload, record_account_health, safe_health_message
from tests.test_db_helper import create_test_engine, init_test_db


class TestAccountHealthClassification(unittest.TestCase):
    def test_classifies_user_meta_and_system_failures(self):
        self.assertEqual(classify_health_error("OAuth token expired")[1], "user")
        self.assertEqual(classify_health_error("Meta Graph rate limit reached")[1], "meta")
        self.assertEqual(classify_health_error("database connection timeout")[1], "system")

    def test_redacts_and_bounds_error_text(self):
        message = safe_health_message("access_token=EAAB-super-secret " + "x" * 500)
        self.assertNotIn("EAAB-super-secret", message)
        self.assertLessEqual(len(message), 240)


class TestAccountHealthPersistence(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_test_engine()
        self.sessions = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)
        await init_test_db(self.engine)
        async with self.sessions() as session:
            user = User(username="health-owner", role="admin", is_approved=True)
            session.add(user)
            await session.flush()
            workspace = Workspace(name="Health", slug="health", owner_user_id=user.id)
            session.add(workspace)
            await session.flush()
            session.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
            account = Account(
                workspace_id=workspace.id,
                owner_user_id=user.id,
                account_id="act_health",
                name="Health account",
            )
            session.add(account)
            await session.commit()
            self.account_id = account.id

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_failure_counter_transition_and_recovery(self):
        async with self.sessions() as session:
            account = await session.get(Account, self.account_id)
            first, transitioned = await record_account_health(
                session, account, success=False, error="Meta Graph unavailable"
            )
            self.assertTrue(transitioned)
            self.assertEqual(first.status, "degraded")
            self.assertEqual(first.cause, "meta")
            await record_account_health(session, account, success=False, error="Meta Graph unavailable")
            third, transitioned = await record_account_health(
                session, account, success=False, error="Meta Graph unavailable"
            )
            self.assertTrue(transitioned)
            self.assertEqual(third.status, "critical")
            recovered, transitioned = await record_account_health(
                session, account, success=True, signals={"token_healthy": True}
            )
            self.assertTrue(transitioned)
            self.assertEqual(recovered.status, "healthy")
            self.assertEqual(recovered.consecutive_failures, 0)
            self.assertTrue(health_payload(recovered)["signals"]["token_healthy"])
