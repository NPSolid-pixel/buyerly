import asyncio
import unittest
from unittest.mock import AsyncMock
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from database.db import Base
from database.models import Account, StoppedAdSet, AppSettings
from rules.engine import RuleEngine, RuleAction
from scheduler.worker import MonitoringWorker
from meta_api.client import MetaClient

class MockMetaClient(MetaClient):
    def __init__(self):
        super().__init__()
        self.adsets_state = {
            "adset_1": {
                "adset_id": "adset_1",
                "adset_name": "Test_Sweden_1",
                "status": "ACTIVE",
                "effective_status": "ACTIVE",
                "spend": 2.50,
                "leads": 0,
                "registrations": 0,
                "total_conversions": 0,
                "cpa": 0.0,
                "impressions": 100,
                "clicks": 5,
                "cpc": 0.5,
                "ctr": 5.0
            },
            "adset_2": {
                "adset_id": "adset_2",
                "adset_name": "Test_Sweden_2",
                "status": "ACTIVE",
                "effective_status": "ACTIVE",
                "spend": 1.00,
                "leads": 0,
                "registrations": 0,
                "total_conversions": 0,
                "cpa": 0.0,
                "impressions": 50,
                "clicks": 2,
                "cpc": 0.5,
                "ctr": 4.0
            }
        }
        self.status_changes = []

    async def get_account_info(self, account_id: str, access_token: str):
        return {"id": account_id, "name": "Underdog 3286", "timezone_name": "HST", "currency": "USD"}

    async def get_adsets_insights(self, account_id: str, access_token: str, date_preset: str = "today"):
        return list(self.adsets_state.values())

    async def set_adset_status(self, adset_id: str, access_token: str, status: str) -> bool:
        self.adsets_state[adset_id]["status"] = status
        self.adsets_state[adset_id]["effective_status"] = status
        self.status_changes.append((adset_id, status))
        return True


class TestEndToEndFlow(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        self.test_session_maker = async_sessionmaker(self.test_engine, class_=AsyncSession, expire_on_commit=False)
        
        async with self.test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        import scheduler.worker as sw
        sw.async_session_maker = self.test_session_maker

        async with self.test_session_maker() as session:
            account = Account(
                account_id="act_e2e_sweden_1083",
                name="Underdog 3286 (Швеция)",
                access_token="mock_token_123",
                owner_id="123456789",
                timezone_name="HST",
                max_spend_0_leads=2.0,
                max_spend_1_lead=6.0,
                max_cpa_multiple_leads=6.0,
                conversion_event="all",
                rules_enabled=True,
                is_active=True
            )
            session.add(account)
            await session.commit()
            self.account_id = account.account_id

    async def asyncTearDown(self):
        await self.test_engine.dispose()

    async def test_rules_disabled_mode_skips_stopping(self):
        async with self.test_session_maker() as session:
            res = await session.execute(select(Account).where(Account.account_id == self.account_id))
            acc = res.scalar_one()
            acc.rules_enabled = False
            await session.commit()

        mock_meta = MockMetaClient()
        sent_alerts = []
        async def mock_notifier(**kwargs):
            sent_alerts.append(kwargs)

        worker = MonitoringWorker(meta_client=mock_meta, telegram_notifier=mock_notifier)
        stats = await worker.run_cycle()

        # Day start should still trigger, but NO adsets should be stopped
        self.assertEqual(stats["starts_notified"], 1)
        self.assertEqual(stats["adsets_stopped"], 0)
        self.assertEqual(mock_meta.adsets_state["adset_1"]["status"], "ACTIVE")
        self.assertEqual(mock_meta.adsets_state["adset_2"]["status"], "ACTIVE")

    async def test_full_lifecycle_with_registrations_and_day_start(self):
        mock_meta = MockMetaClient()
        sent_alerts = []

        async def mock_notifier(**kwargs):
            sent_alerts.append(kwargs)

        worker = MonitoringWorker(meta_client=mock_meta, telegram_notifier=mock_notifier)

        # ----------------------------------------------------
        # ЦИКЛ 1: Старт дня (Spend > 0) + adset_1 потратил $2.50 без лидов -> PAUSE
        # ----------------------------------------------------
        stats_1 = await worker.run_cycle()
        self.assertEqual(stats_1["starts_notified"], 1)
        self.assertEqual(stats_1["adsets_stopped"], 1)
        self.assertEqual(mock_meta.adsets_state["adset_1"]["status"], "PAUSED")
        self.assertEqual(mock_meta.adsets_state["adset_2"]["status"], "ACTIVE")

        # Проверяем, что пришел алерт о старте открута и алерт о стопе
        event_types = [a["event_type"] for a in sent_alerts]
        self.assertIn("DAY_START", event_types)
        self.assertIn("STOP", event_types)

        # ----------------------------------------------------
        # ЦИКЛ 2: в adset_1 долетела РЕГИСТРАЦИЯ! (spend $2.50, registrations 1)
        # ----------------------------------------------------
        mock_meta.adsets_state["adset_1"]["registrations"] = 1
        mock_meta.adsets_state["adset_1"]["total_conversions"] = 1
        mock_meta.adsets_state["adset_1"]["cpa"] = 2.50

        stats_2 = await worker.run_cycle()
        self.assertEqual(stats_2["proposals_sent"], 1)

        reactivate_alerts = [a for a in sent_alerts if a["event_type"] == "PROPOSE_REACTIVATE"]
        self.assertEqual(len(reactivate_alerts), 1)
        self.assertIn("Долетел(а)", reactivate_alerts[0]["eval_result"].reason)

        # ----------------------------------------------------
        # ДЕЙСТВИЕ ПОЛЬЗОВАТЕЛЯ: Нажатие кнопки "Включить"
        # ----------------------------------------------------
        await mock_meta.set_adset_status("adset_1", "mock_token_123", "ACTIVE")
        async with self.test_session_maker() as session:
            res = await session.execute(
                select(StoppedAdSet).where(StoppedAdSet.adset_id == "adset_1")
            )
            stopped_entry = res.scalar_one_or_none()
            stopped_entry.is_resolved = True
            await session.commit()

        self.assertEqual(mock_meta.adsets_state["adset_1"]["status"], "ACTIVE")
        print("Full E2E test with day start & registration reactivation passed!")

    async def test_account_disabled_alert(self):
        mock_meta = MockMetaClient()
        mock_meta.get_account_info = AsyncMock(return_value={
            "id": "act_e2e_sweden_1083",
            "name": "Underdog 3286",
            "account_status": 2, # Disabled
            "status_label": "🔴 Заблокирован в Meta (DISABLED / Policy Ban)",
            "timezone_name": "HST"
        })
        sent_alerts = []

        async def mock_notifier(**kwargs):
            sent_alerts.append(kwargs)

        worker = MonitoringWorker(meta_client=mock_meta, telegram_notifier=mock_notifier)
        stats = await worker.run_cycle()

        self.assertEqual(len(sent_alerts), 1)
        self.assertEqual(sent_alerts[0]["event_type"], "ACCOUNT_ISSUE")
        self.assertIn("Заблокирован", sent_alerts[0]["local_time"])

    async def test_token_expired_alert(self):
        mock_meta = MockMetaClient()
        mock_meta.get_account_info = AsyncMock(side_effect=PermissionError("Token expired"))
        sent_alerts = []

        async def mock_notifier(**kwargs):
            sent_alerts.append(kwargs)

        worker = MonitoringWorker(meta_client=mock_meta, telegram_notifier=mock_notifier)
        stats = await worker.run_cycle()

        self.assertEqual(len(sent_alerts), 1)
        self.assertEqual(sent_alerts[0]["event_type"], "TOKEN_EXPIRED")

if __name__ == "__main__":
    unittest.main()
