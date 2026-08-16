import asyncio
import unittest
from unittest.mock import AsyncMock
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from database.db import Base
from database.models import Account, AppSettings
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
                "spend": 15.50,
                "leads": 0,
                "registrations": 0,
                "total_conversions": 0,
                "cpa": 0.0,
                "impressions": 100,
                "clicks": 5,
                "cpc": 0.5,
                "ctr": 5.0,
                "daily_budget": 50.0,
                "purchases": 0
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
                "ctr": 4.0,
                "daily_budget": 30.0,
                "purchases": 0
            }
        }
        self.status_changes = []
        self.budget_changes = []

    async def get_account_info(self, account_id: str, access_token: str):
        return {"id": account_id, "name": "Underdog 3286", "timezone_name": "HST", "currency": "USD", "account_status": 1, "status_label": "🟢 Активен (ACTIVE)"}

    async def get_adsets_insights(self, account_id: str, access_token: str, date_preset: str = "today"):
        return list(self.adsets_state.values())

    async def set_adset_status(self, adset_id: str, access_token: str, status: str) -> bool:
        self.adsets_state[adset_id]["status"] = status
        self.adsets_state[adset_id]["effective_status"] = status
        self.status_changes.append((adset_id, status))
        return True

    async def update_adset_budget(self, adset_id: str, access_token: str, new_daily_budget_dollars: float) -> bool:
        self.adsets_state[adset_id]["daily_budget"] = new_daily_budget_dollars
        self.budget_changes.append((adset_id, new_daily_budget_dollars))
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
                rule_action="turn_off",
                rule_conditions='[{"metric": "spend", "operator": "gte", "value": 10.0, "time_window": "today"}, {"metric": "leads", "operator": "eq", "value": 0.0, "time_window": "today"}]',
                rule_condition_logic="and",
                rules_enabled=True,
                is_active=True
            )
            session.add(account)
            await session.commit()
            self.account_id = account.account_id

    async def asyncTearDown(self):
        await self.test_engine.dispose()

    async def test_rules_disabled_mode_skips_stopping(self):
        """Если авто-правила выключены, адсеты не должны останавливаться."""
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

    async def test_custom_rule_stops_adset(self):
        """Пользовательское правило: Спенд >= $10 И Лиды = 0 → STOP."""
        mock_meta = MockMetaClient()
        sent_alerts = []

        async def mock_notifier(**kwargs):
            sent_alerts.append(kwargs)

        worker = MonitoringWorker(meta_client=mock_meta, telegram_notifier=mock_notifier)

        # adset_1: spend=$15.50, leads=0 → match → STOP
        # adset_2: spend=$1.00, leads=0 → spend < $10 → NOOP
        stats = await worker.run_cycle()
        self.assertEqual(stats["adsets_stopped"], 1)
        self.assertEqual(mock_meta.adsets_state["adset_1"]["status"], "PAUSED")
        self.assertEqual(mock_meta.adsets_state["adset_2"]["status"], "ACTIVE")

        event_types = [a["event_type"] for a in sent_alerts]
        self.assertIn("DAY_START", event_types)
        self.assertIn("STOP", event_types)

    async def test_budget_increase_action(self):
        """Правило: CPL < $5 И Лиды >= 2 → увеличить бюджет на 20%, потолок $100."""
        async with self.test_session_maker() as session:
            res = await session.execute(select(Account).where(Account.account_id == self.account_id))
            acc = res.scalar_one()
            acc.rule_action = "increase_budget"
            acc.rule_conditions = '[{"metric": "cpl", "operator": "lt", "value": 5.0}, {"metric": "leads", "operator": "gte", "value": 2.0}]'
            acc.rule_condition_logic = "and"
            acc.rule_budget_change_percent = 20.0
            acc.rule_budget_max_daily = 100.0
            await session.commit()

        mock_meta = MockMetaClient()
        # adset_1: spend=$15.50, leads=0, CPL=$15.50 → no match (leads < 2)
        # Set adset_2 to match: spend=$8, leads=3 → CPL=$2.67 < $5, leads=3 >= 2 → match
        mock_meta.adsets_state["adset_2"]["spend"] = 8.0
        mock_meta.adsets_state["adset_2"]["leads"] = 3
        mock_meta.adsets_state["adset_2"]["daily_budget"] = 50.0

        sent_alerts = []
        async def mock_notifier(**kwargs):
            sent_alerts.append(kwargs)

        worker = MonitoringWorker(meta_client=mock_meta, telegram_notifier=mock_notifier)
        stats = await worker.run_cycle()

        self.assertGreaterEqual(stats.get("budgets_changed", 0), 1)
        # Check that the budget was increased: $50 * 1.2 = $60, within cap of $100
        self.assertEqual(len(mock_meta.budget_changes), 1)
        self.assertEqual(mock_meta.budget_changes[0][0], "adset_2")
        self.assertAlmostEqual(mock_meta.budget_changes[0][1], 60.0, places=1)

    async def test_account_disabled_alert(self):
        """Если кабинет заблокирован в Meta → алерт ACCOUNT_ISSUE."""
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
        """Если токен Meta слетел → алерт TOKEN_EXPIRED."""
        mock_meta = MockMetaClient()
        mock_meta.get_account_info = AsyncMock(side_effect=PermissionError("Token expired"))
        sent_alerts = []

        async def mock_notifier(**kwargs):
            sent_alerts.append(kwargs)

        worker = MonitoringWorker(meta_client=mock_meta, telegram_notifier=mock_notifier)
        stats = await worker.run_cycle()

        self.assertEqual(len(sent_alerts), 1)
        self.assertEqual(sent_alerts[0]["event_type"], "TOKEN_EXPIRED")

    def test_meta_client_usage_headers_parsing(self):
        """Проверка парсинга заголовков X-Business-Use-Case-Usage."""
        import httpx
        client = MetaClient()
        
        # 1. Нормальный расход (15%)
        headers_normal = httpx.Headers({
            "x-business-use-case-usage": '{"act_1083": [{"type": "ads_management", "call_count": 15, "total_cputime": 10, "total_time": 8, "estimated_time_to_regain_access": 0}]}'
        })
        res_normal = client._parse_usage_headers(headers_normal, "act_1083")
        self.assertEqual(res_normal["call_count"], 15)
        self.assertFalse(res_normal["is_high_usage"])

        # 2. Высокий расход (85% -> Warning trigger)
        headers_high = httpx.Headers({
            "x-business-use-case-usage": '{"act_1083": [{"type": "ads_management", "call_count": 85, "total_cputime": 60, "total_time": 50, "estimated_time_to_regain_access": 5}]}'
        })
        res_high = client._parse_usage_headers(headers_high, "act_1083")
        self.assertEqual(res_high["call_count"], 85)
        self.assertTrue(res_high["is_high_usage"])
        self.assertEqual(res_high["estimated_time_to_regain_access"], 5)

if __name__ == "__main__":
    unittest.main()
