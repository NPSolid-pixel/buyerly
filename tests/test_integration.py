import asyncio
import json
import unittest
from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from database.db import Base
from database.models import (
    Account,
    AppSettings,
    AuditEvent,
    AutomationScheduleState,
    RuleExecutionState,
    StoppedAdSet,
    User,
    Workspace,
    WorkspaceMember,
)
from rules.engine import RuleEngine, RuleAction
from scheduler.worker import MonitoringWorker
from meta_api.client import MetaClient


def _json_val(v):
    return json.loads(v) if isinstance(v, str) else (v or {})


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
                "impressions": 50,
                "clicks": 2,
                "cpc": 0.5,
                "ctr": 4.0,
                "daily_budget": 30.0,
                "purchases": 0
            }
        }
        self.insights_by_window = {}
        self.requested_windows = []
        self.status_changes = []
        self.budget_changes = []

    async def get_account_info(
        self,
        account_id: str,
        access_token: str,
        *,
        priority: str = "normal",
    ):
        return {"id": account_id, "name": "Underdog 3286", "timezone_name": "HST", "currency": "USD", "account_status": 1, "status_label": "Активен (ACTIVE)"}

    async def get_adsets_insights(
        self,
        account_id: str,
        access_token: str,
        date_preset: str = "today",
        currency: str = "UNKNOWN",
        priority: str = "normal",
    ):
        self.requested_windows.append(date_preset)
        source = self.insights_by_window.get(date_preset, self.adsets_state)
        return [dict(adset) for adset in source.values()]

    async def set_adset_status(self, adset_id: str, access_token: str, status: str, *args, **kwargs) -> bool:
        self.adsets_state[adset_id]["status"] = status
        self.adsets_state[adset_id]["effective_status"] = status
        self.status_changes.append((adset_id, status))
        return True

    async def update_adset_budget(
        self,
        adset_id: str,
        access_token: str,
        new_daily_budget_dollars: float,
        currency: str = "UNKNOWN",
        *args,
        **kwargs,
    ) -> bool:
        self.adsets_state[adset_id]["daily_budget"] = new_daily_budget_dollars
        self.budget_changes.append((adset_id, new_daily_budget_dollars))
        return True


from tests.test_db_helper import create_test_engine, init_test_db


class TestEndToEndFlow(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.test_engine = create_test_engine()
        self.test_session_maker = async_sessionmaker(self.test_engine, class_=AsyncSession, expire_on_commit=False)
        await init_test_db(self.test_engine)

        import scheduler.worker as sw
        sw.async_session_maker = self.test_session_maker

        async with self.test_session_maker() as session:
            user = User(
                telegram_id="123456789",
                username="e2e_user",
                full_name="E2E User",
                role="admin",
                is_approved=True,
            )
            session.add(user)
            await session.flush()

            ws = Workspace(
                name="E2E Workspace",
                slug="e2e-workspace",
                badge_text="E",
                badge_color="#3B82F6",
                owner_user_id=user.id,
            )
            session.add(ws)
            await session.flush()
            session.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner"))
            user.active_workspace_id = ws.id

            account = Account(
                account_id="act_e2e_sweden_1083",
                name="Underdog 3286 (Швеция)",
                access_token="mock_token_123",
                owner_user_id=user.id,
                workspace_id=ws.id,
                timezone_name="HST",
                currency="USD",
                active_rules=json.dumps([
                    {
                        "preset_id": 1,
                        "name": "Stop spend without leads",
                        "action": "turn_off",
                        "conditions": [
                            {"metric": "spend", "operator": "gte", "value": 10.0, "time_window": "today"},
                            {"metric": "leads", "operator": "eq", "value": 0.0, "time_window": "today"},
                        ],
                        "logic": "and",
                        "cooldown_minutes": 0,
                        "notify_tg": True,
                        "budget_change_percent": 0.0,
                        "budget_max_daily": 0.0,
                    }
                ]),
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

        # Calendar day notifications are handled by a separate minute job.
        self.assertEqual(sent_alerts, [])
        self.assertEqual(stats["adsets_stopped"], 0)
        self.assertEqual(mock_meta.adsets_state["adset_1"]["status"], "ACTIVE")
        self.assertEqual(mock_meta.adsets_state["adset_2"]["status"], "ACTIVE")

    async def test_custom_rule_stops_adset(self):
        """Пользовательское правило: Спенд >= $10 И Лиды = 0 → STOP."""
        async with self.test_session_maker() as session:
            session.add(AppSettings(stop_confirmation_minutes=0))
            await session.commit()
        mock_meta = MockMetaClient()
        sent_alerts = []
        now = [10_000.0]

        async def mock_notifier(**kwargs):
            sent_alerts.append(kwargs)

        worker = MonitoringWorker(
            meta_client=mock_meta,
            telegram_notifier=mock_notifier,
            clock=lambda: now[0],
        )

        # adset_1: spend=$15.50, leads=0 → match → STOP
        # adset_2: spend=$1.00, leads=0 → spend < $10 → NOOP
        stats = await worker.run_cycle()
        self.assertEqual(stats["adsets_stopped"], 1)
        self.assertEqual(mock_meta.adsets_state["adset_1"]["status"], "PAUSED")
        self.assertEqual(mock_meta.adsets_state["adset_2"]["status"], "ACTIVE")

        event_types = [a["event_type"] for a in sent_alerts]
        self.assertIn("STOP", event_types)

        async with self.test_session_maker() as session:
            stored = (await session.execute(select(StoppedAdSet))).scalars().all()
            self.assertEqual(len(stored), 1)
            self.assertEqual(stored[0].adset_id, "adset_1")
            self.assertEqual(stored[0].stop_spend, 15.5)
            self.assertFalse(stored[0].is_resolved)

            stop_events = (
                await session.execute(
                    select(AuditEvent).where(AuditEvent.event_type == "STOP")
                )
            ).scalars().all()
            self.assertEqual(len(stop_events), 1)
            self.assertEqual(stop_events[0].status, "SUCCESS")
            # owner_id removed in favor of owner_user_id
            self.assertEqual(stop_events[0].rule_id, 1)
            self.assertEqual(stop_events[0].rule_name, "Stop spend without leads")
            self.assertEqual(_json_val(stop_events[0].before_state)["status"], "ACTIVE")
            self.assertEqual(_json_val(stop_events[0].after_state)["status"], "PAUSED")
            self.assertTrue(stop_events[0].correlation_id)

        # Re-stopping the same ad set reopens and updates one durable record.
        mock_meta.adsets_state["adset_1"]["status"] = "ACTIVE"
        mock_meta.adsets_state["adset_1"]["effective_status"] = "ACTIVE"
        mock_meta.adsets_state["adset_1"]["spend"] = 20.0
        now[0] += 10 * 60
        second_worker = MonitoringWorker(meta_client=mock_meta, clock=lambda: now[0])
        await second_worker.run_cycle()

        async with self.test_session_maker() as session:
            stored = (await session.execute(select(StoppedAdSet))).scalars().all()
            self.assertEqual(len(stored), 1)
            self.assertEqual(stored[0].stop_spend, 20.0)
            self.assertFalse(stored[0].is_resolved)

    async def test_new_account_day_uses_meta_timezone_without_spend_or_rules(self):
        hawaii = ZoneInfo("Pacific/Honolulu")
        now = [datetime(2026, 8, 18, 0, 0, 30, tzinfo=hawaii).timestamp()]
        async with self.test_session_maker() as session:
            account = (
                await session.execute(select(Account).where(Account.account_id == self.account_id))
            ).scalar_one()
            account.timezone_name = "US/Hawaii"
            account.last_day_start_date = "2026-08-17"
            account.rules_enabled = False
            account.is_active = False
            await session.commit()

        sent_alerts = []

        async def mock_notifier(**kwargs):
            sent_alerts.append(kwargs)

        meta_client = MockMetaClient()
        meta_client.get_account_info = AsyncMock()
        worker = MonitoringWorker(
            meta_client=meta_client,
            telegram_notifier=mock_notifier,
            clock=lambda: now[0],
        )
        stats = await worker.run_day_boundary_cycle()

        self.assertEqual(stats["days_notified"], 1)
        self.assertEqual(stats["invalid_timezones"], 0)
        self.assertEqual(len(sent_alerts), 1)
        self.assertEqual(sent_alerts[0]["event_type"], "ACCOUNT_DAY_STARTED")
        self.assertEqual(sent_alerts[0]["local_time"], "00:00")
        self.assertEqual(sent_alerts[0]["local_date"], "18.08.2026")
        self.assertEqual(sent_alerts[0]["timezone_name"], "Pacific/Honolulu")
        self.assertEqual(sent_alerts[0]["utc_offset"], "UTC−10:00")
        self.assertNotIn("start_spend", sent_alerts[0])
        meta_client.get_account_info.assert_not_awaited()

        now[0] += 60
        restarted_worker = MonitoringWorker(
            meta_client=meta_client,
            telegram_notifier=mock_notifier,
            clock=lambda: now[0],
        )
        repeated = await restarted_worker.run_day_boundary_cycle()
        self.assertEqual(repeated["days_notified"], 0)
        self.assertEqual(len(sent_alerts), 1)

        async with self.test_session_maker() as session:
            account = (
                await session.execute(select(Account).where(Account.account_id == self.account_id))
            ).scalar_one()
            self.assertEqual(account.timezone_name, "Pacific/Honolulu")
            self.assertEqual(account.last_day_start_date, "2026-08-18")
            events = (
                await session.execute(
                    select(AuditEvent).where(AuditEvent.event_type == "ACCOUNT_DAY_STARTED")
                )
            ).scalars().all()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].status, "SUCCESS")

    async def test_first_observation_anchors_date_without_midday_notification(self):
        hawaii = ZoneInfo("Pacific/Honolulu")
        now = datetime(2026, 8, 18, 12, 30, tzinfo=hawaii).timestamp()
        sent_alerts = []

        async def mock_notifier(**kwargs):
            sent_alerts.append(kwargs)

        worker = MonitoringWorker(
            telegram_notifier=mock_notifier,
            clock=lambda: now,
        )

        stats = await worker.run_day_boundary_cycle()

        self.assertEqual(stats["dates_initialized"], 1)
        self.assertEqual(stats["days_notified"], 0)
        self.assertEqual(sent_alerts, [])
        async with self.test_session_maker() as session:
            account = (
                await session.execute(select(Account).where(Account.account_id == self.account_id))
            ).scalar_one()
            self.assertEqual(account.last_day_start_date, "2026-08-18")

    async def test_historical_window_uses_current_adset_metrics(self):
        """Yesterday metrics are matched by ad set instead of as one account-wide map."""
        async with self.test_session_maker() as session:
            res = await session.execute(select(Account).where(Account.account_id == self.account_id))
            acc = res.scalar_one()
            acc.active_rules = json.dumps([
                {
                    "preset_id": 3,
                    "name": "Stop yesterday spend",
                    "action": "turn_off",
                    "conditions": [
                        {
                            "metric": "spend",
                            "operator": "gte",
                            "value": 20.0,
                            "time_window": "yesterday",
                        }
                    ],
                    "logic": "and",
                    "cooldown_minutes": 0,
                    "notify_tg": False,
                }
            ])
            session.add(AppSettings(stop_confirmation_minutes=0))
            await session.commit()

        mock_meta = MockMetaClient()
        mock_meta.adsets_state["adset_1"]["spend"] = 1.0
        mock_meta.adsets_state["adset_2"]["spend"] = 1.0
        mock_meta.insights_by_window["yesterday"] = {
            "adset_1": {**mock_meta.adsets_state["adset_1"], "spend": 25.0},
            "adset_2": {**mock_meta.adsets_state["adset_2"], "spend": 2.0},
        }

        worker = MonitoringWorker(meta_client=mock_meta)
        stats = await worker.run_cycle()

        self.assertIn("yesterday", mock_meta.requested_windows)
        self.assertEqual(stats["adsets_stopped"], 1)
        self.assertEqual(mock_meta.status_changes, [("adset_1", "PAUSED")])

        async with self.test_session_maker() as session:
            audit_event = (
                await session.execute(
                    select(AuditEvent).where(AuditEvent.event_type == "STOP")
                )
            ).scalar_one()
            self.assertEqual(audit_event.status, "SUCCESS")
            self.assertEqual(audit_event.rule_id, 3)
            self.assertFalse(_json_val(audit_event.details)["notify_tg"])

    async def test_failed_rule_action_is_audited_and_secret_safe(self):
        async with self.test_session_maker() as session:
            session.add(AppSettings(stop_confirmation_minutes=0))
            await session.commit()
        mock_meta = MockMetaClient()
        mock_meta.set_adset_status = AsyncMock(
            side_effect=RuntimeError("Meta failed: access_token=private-secret")
        )

        worker = MonitoringWorker(meta_client=mock_meta)
        stats = await worker.run_cycle()

        self.assertEqual(stats["adsets_stopped"], 0)
        self.assertTrue(any("Pause error adset_1" in error for error in stats["errors"]))

        async with self.test_session_maker() as session:
            failed_event = (
                await session.execute(
                    select(AuditEvent).where(
                        AuditEvent.event_type == "STOP",
                        AuditEvent.status == "ERROR",
                    )
                )
            ).scalar_one()
            self.assertNotIn("private-secret", failed_event.message)
            self.assertIn("access_token=[REDACTED]", failed_event.message)
            self.assertEqual(_json_val(failed_event.before_state)["status"], "ACTIVE")

    async def test_stop_requires_repeated_confirmation_before_meta_mutation(self):
        now = [1_000.0]
        async with self.test_session_maker() as session:
            session.add(
                AppSettings(
                    critical_rule_interval_minutes=2,
                    stop_confirmation_minutes=5,
                )
            )
            await session.commit()

        mock_meta = MockMetaClient()
        worker = MonitoringWorker(meta_client=mock_meta, clock=lambda: now[0])

        first = await worker.run_cycle()
        self.assertEqual(first["adsets_stopped"], 0)
        self.assertEqual(first["stop_confirmations_waiting"], 1)
        self.assertEqual(mock_meta.status_changes, [])

        now[0] += 4 * 60
        second = await worker.run_cycle()
        self.assertEqual(second["adsets_stopped"], 0)
        self.assertEqual(second["stop_confirmations_waiting"], 1)

        now[0] += 2 * 60
        third = await worker.run_cycle()
        self.assertEqual(third["adsets_stopped"], 1)
        self.assertEqual(mock_meta.status_changes, [("adset_1", "PAUSED")])

        async with self.test_session_maker() as session:
            pending_event = (
                await session.execute(
                    select(AuditEvent).where(
                        AuditEvent.event_type == "STOP_CONFIRMATION_STARTED"
                    )
                )
            ).scalar_one()
            self.assertEqual(pending_event.status, "WAITING")

    async def test_stop_confirmation_restarts_when_condition_match_breaks(self):
        now = [2_000.0]
        async with self.test_session_maker() as session:
            session.add(
                AppSettings(
                    critical_rule_interval_minutes=2,
                    stop_confirmation_minutes=5,
                )
            )
            await session.commit()

        mock_meta = MockMetaClient()
        worker = MonitoringWorker(meta_client=mock_meta, clock=lambda: now[0])

        first = await worker.run_cycle()
        self.assertEqual(first["stop_confirmations_waiting"], 1)

        now[0] += 4 * 60
        mock_meta.adsets_state["adset_1"]["leads"] = 1
        unmatched = await worker.run_cycle()
        self.assertEqual(unmatched["adsets_stopped"], 0)
        self.assertEqual(mock_meta.status_changes, [])

        now[0] += 2 * 60
        mock_meta.adsets_state["adset_1"]["leads"] = 0
        restarted = await worker.run_cycle()
        self.assertEqual(restarted["adsets_stopped"], 0)
        self.assertEqual(restarted["stop_confirmations_waiting"], 1)

        async with self.test_session_maker() as session:
            state = (
                await session.execute(
                    select(RuleExecutionState).where(
                        RuleExecutionState.adset_id == "adset_1",
                        RuleExecutionState.action == RuleAction.STOP.value,
                    )
                )
            ).scalar_one()
            details = _json_val(state.details)
            self.assertEqual(state.status, "STOP_CONFIRMING")
            self.assertEqual(details["first_seen_at"], now[0])
            self.assertEqual(details["observations"], 1)

    async def test_rule_check_interval_is_enforced_without_extra_meta_calls(self):
        async with self.test_session_maker() as session:
            res = await session.execute(select(Account).where(Account.account_id == self.account_id))
            acc = res.scalar_one()
            acc.active_rules = json.dumps([
                {
                    "preset_id": 4,
                    "name": "Five minute notification",
                    "action": "notify_only",
                    "conditions": [
                        {"metric": "spend", "operator": "gte", "value": 0.0}
                    ],
                    "logic": "and",
                    "check_interval": 5,
                    "cooldown_minutes": 0,
                    "notify_tg": False,
                }
            ])
            await session.commit()

        now = [1_000.0]
        mock_meta = MockMetaClient()
        worker = MonitoringWorker(meta_client=mock_meta, clock=lambda: now[0])

        first = await worker.run_cycle()
        initial_request_count = len(mock_meta.requested_windows)
        self.assertEqual(first["rules_checked"], 1)

        now[0] += 4 * 60
        # A new worker instance must read the same persisted schedule state.
        restarted_worker = MonitoringWorker(meta_client=mock_meta, clock=lambda: now[0])
        early = await restarted_worker.run_cycle()
        self.assertEqual(early["accounts_skipped"], 1)
        self.assertEqual(len(mock_meta.requested_windows), initial_request_count)

        now[0] += 60
        due = await restarted_worker.run_cycle()
        self.assertEqual(due["rules_checked"], 1)
        self.assertGreater(len(mock_meta.requested_windows), initial_request_count)

    async def test_stop_rule_uses_protected_critical_interval(self):
        async with self.test_session_maker() as session:
            account = (
                await session.execute(select(Account).where(Account.account_id == self.account_id))
            ).scalar_one()
            rules = json.loads(account.active_rules)
            rules[0]["check_interval"] = 60
            account.active_rules = json.dumps(rules)
            await session.commit()

        now = [1_000.0]
        mock_meta = MockMetaClient()
        worker = MonitoringWorker(meta_client=mock_meta, clock=lambda: now[0])

        first = await worker.run_cycle()
        self.assertEqual(first["rules_checked"], 1)

        now[0] += 119
        early = await worker.run_cycle()
        self.assertEqual(early["rules_checked"], 0)

        now[0] += 1
        due = await worker.run_cycle()
        self.assertEqual(due["rules_checked"], 1)

    async def test_account_reads_use_bounded_parallelism(self):
        class TrackingMetaClient(MockMetaClient):
            def __init__(self):
                super().__init__()
                self.active_reads = 0
                self.max_active_reads = 0

            async def get_account_info(
                self,
                account_id: str,
                access_token: str,
                *,
                priority: str = "normal",
            ):
                self.active_reads += 1
                self.max_active_reads = max(self.max_active_reads, self.active_reads)
                try:
                    await asyncio.sleep(0.03)
                    return await super().get_account_info(
                        account_id,
                        access_token,
                        priority=priority,
                    )
                finally:
                    self.active_reads -= 1

        async with self.test_session_maker() as session:
            primary = (
                await session.execute(select(Account).where(Account.account_id == self.account_id))
            ).scalar_one()
            primary.rules_enabled = False
            for suffix in ("2", "3"):
                session.add(
                    Account(
                        account_id=f"act_parallel_{suffix}",
                        name=f"Parallel {suffix}",
                        access_token=f"token_{suffix}",
                        timezone_name="UTC",
                        currency="USD",
                        active_rules="[]",
                        rules_enabled=False,
                        is_active=True,
                    )
                )
            session.add(AppSettings(max_concurrent_accounts=2))
            await session.commit()

        mock_meta = TrackingMetaClient()
        worker = MonitoringWorker(meta_client=mock_meta)
        stats = await worker.run_cycle()

        self.assertEqual(stats["accounts_checked"], 3)
        self.assertEqual(mock_meta.max_active_reads, 2)

    async def test_unknown_currency_is_refreshed_before_next_scheduled_check(self):
        now = [1_000.0]
        async with self.test_session_maker() as session:
            account = (
                await session.execute(select(Account).where(Account.account_id == self.account_id))
            ).scalar_one()
            account.currency = "UNKNOWN"
            account.rules_enabled = False
            session.add(
                AutomationScheduleState(
                    state_key=MonitoringWorker._schedule_key("account", account.account_id),
                    account_id=account.account_id,
                    last_checked_at=now[0],
                )
            )
            await session.commit()

        mock_meta = MockMetaClient()
        worker = MonitoringWorker(meta_client=mock_meta, clock=lambda: now[0])
        stats = await worker.run_cycle()

        self.assertEqual(stats["accounts_checked"], 1)
        self.assertEqual(stats["accounts_skipped"], 0)
        async with self.test_session_maker() as session:
            account = (
                await session.execute(select(Account).where(Account.account_id == self.account_id))
            ).scalar_one()
            self.assertEqual(account.currency, "USD")

    async def test_only_rules_that_are_due_are_evaluated(self):
        async with self.test_session_maker() as session:
            res = await session.execute(select(Account).where(Account.account_id == self.account_id))
            acc = res.scalar_one()
            acc.active_rules = json.dumps([
                {
                    "preset_id": 5,
                    "name": "Fast notification",
                    "action": "notify_only",
                    "conditions": [
                        {"metric": "spend", "operator": "gte", "value": 0.0}
                    ],
                    "logic": "and",
                    "check_interval": 5,
                    "notify_tg": False,
                },
                {
                    "preset_id": 6,
                    "name": "Slow stop",
                    "action": "turn_off",
                    "conditions": [
                        {"metric": "spend", "operator": "gte", "value": 10.0}
                    ],
                    "logic": "and",
                    "check_interval": 60,
                    "notify_tg": False,
                },
            ])
            await session.commit()

        now = [2_000.0]
        mock_meta = MockMetaClient()
        worker = MonitoringWorker(meta_client=mock_meta, clock=lambda: now[0])
        async with self.test_session_maker() as session:
            session.add(
                AutomationScheduleState(
                    state_key=f"rule:{self.account_id}:{self.account_id}:6",
                    account_id=self.account_id,
                    rule_key=f"{self.account_id}:6",
                    last_checked_at=now[0],
                )
            )
            await session.commit()

        stats = await worker.run_cycle()

        self.assertEqual(stats["rules_checked"], 1)
        self.assertEqual(mock_meta.status_changes, [])

    async def test_budget_action_is_not_repeated_after_worker_restart(self):
        async with self.test_session_maker() as session:
            account = (
                await session.execute(select(Account).where(Account.account_id == self.account_id))
            ).scalar_one()
            account.active_rules = json.dumps([
                {
                    "preset_id": 22,
                    "name": "Durable scale",
                    "action": "increase_budget",
                    "conditions": [{"metric": "leads", "operator": "gte", "value": 1.0}],
                    "logic": "and",
                    "check_interval": 5,
                    "cooldown_minutes": 30,
                    "notify_tg": False,
                    "budget_change_percent": 20.0,
                    "budget_max_daily": 100.0,
                }
            ])
            await session.commit()

        now = [5_000.0]
        mock_meta = MockMetaClient()
        mock_meta.adsets_state["adset_2"]["leads"] = 2
        first_worker = MonitoringWorker(meta_client=mock_meta, clock=lambda: now[0])
        first = await first_worker.run_cycle()
        self.assertEqual(first["budgets_changed"], 1)
        self.assertEqual(len(mock_meta.budget_changes), 1)

        now[0] += 5 * 60
        restarted_worker = MonitoringWorker(meta_client=mock_meta, clock=lambda: now[0])
        second = await restarted_worker.run_cycle()

        self.assertEqual(second["budgets_changed"], 0)
        self.assertGreaterEqual(second["actions_skipped"], 1)
        self.assertEqual(len(mock_meta.budget_changes), 1)
        async with self.test_session_maker() as session:
            execution = (await session.execute(select(RuleExecutionState))).scalars().all()
            self.assertTrue(any(row.status == "SUCCESS" for row in execution))

    async def test_pending_budget_action_is_reconciled_from_meta_state(self):
        now = [8_000.0]
        mock_meta = MockMetaClient()
        mock_meta.adsets_state["adset_2"]["leads"] = 2
        mock_meta.adsets_state["adset_2"]["daily_budget"] = 60.0

        async with self.test_session_maker() as session:
            account = (
                await session.execute(select(Account).where(Account.account_id == self.account_id))
            ).scalar_one()
            account.active_rules = json.dumps([
                {
                    "preset_id": 23,
                    "name": "Reconcile scale",
                    "action": "increase_budget",
                    "conditions": [{"metric": "leads", "operator": "gte", "value": 1.0}],
                    "logic": "and",
                    "check_interval": 5,
                    "cooldown_minutes": 30,
                    "notify_tg": False,
                    "budget_change_percent": 20.0,
                    "budget_max_daily": 100.0,
                }
            ])
            evaluation = RuleEngine.evaluate(mock_meta.adsets_state["adset_2"], account)
            execution_key, rule_key = MonitoringWorker._execution_key(account, evaluation)
            session.add(
                RuleExecutionState(
                    execution_key=execution_key,
                    account_id=account.account_id,
                    adset_id="adset_2",
                    rule_key=rule_key,
                    action=RuleAction.INCREASE_BUDGET.value,
                    status="PENDING",
                    correlation_id="crashed-cycle",
                    last_attempt_at=now[0] - 60,
                    before_state='{"daily_budget":50.0}',
                    after_state='{"daily_budget":60.0}',
                )
            )
            await session.commit()

        restarted_worker = MonitoringWorker(meta_client=mock_meta, clock=lambda: now[0])
        stats = await restarted_worker.run_cycle()

        self.assertEqual(stats["budgets_changed"], 0)
        self.assertEqual(stats["actions_reconciled"], 1)
        self.assertEqual(mock_meta.budget_changes, [])
        async with self.test_session_maker() as session:
            state = (await session.execute(select(RuleExecutionState))).scalar_one()
            self.assertEqual(state.status, "SUCCESS")
            self.assertEqual(_json_val(state.details)["reconciled_after_restart"], True)

    async def test_budget_increase_action(self):
        """Правило: CPL < $5 И Лиды >= 2 → увеличить бюджет на 20%, потолок $100."""
        async with self.test_session_maker() as session:
            res = await session.execute(select(Account).where(Account.account_id == self.account_id))
            acc = res.scalar_one()
            acc.active_rules = json.dumps([
                {
                    "preset_id": 2,
                    "name": "Scale good CPL",
                    "action": "increase_budget",
                    "conditions": [
                        {"metric": "cpl", "operator": "lt", "value": 5.0},
                        {"metric": "leads", "operator": "gte", "value": 2.0},
                    ],
                    "logic": "and",
                    "cooldown_minutes": 0,
                    "notify_tg": True,
                    "budget_change_percent": 20.0,
                    "budget_max_daily": 100.0,
                }
            ])
            await session.commit()

        mock_meta = MockMetaClient()
        # adset_1: spend=$15.50, leads=0, CPL unavailable → no match.
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

    async def test_turn_on_rule_reactivates_paused_adset(self):
        async with self.test_session_maker() as session:
            res = await session.execute(select(Account).where(Account.account_id == self.account_id))
            acc = res.scalar_one()
            acc.active_rules = json.dumps([
                {
                    "preset_id": 7,
                    "name": "Reactivate after lead",
                    "action": "turn_on",
                    "conditions": [
                        {"metric": "leads", "operator": "gte", "value": 1.0}
                    ],
                    "logic": "and",
                    "check_interval": 5,
                    "notify_tg": False,
                }
            ])
            session.add(
                StoppedAdSet(
                    account_id=self.account_id,
                    adset_id="adset_1",
                    adset_name="Test_Sweden_1",
                    stop_spend=5.0,
                    stop_leads=0,
                    stop_registrations=0,
                    is_resolved=False,
                )
            )
            await session.commit()

        mock_meta = MockMetaClient()
        mock_meta.adsets_state["adset_1"]["status"] = "PAUSED"
        mock_meta.adsets_state["adset_1"]["effective_status"] = "PAUSED"
        mock_meta.adsets_state["adset_1"]["leads"] = 2

        sent_alerts = []

        async def mock_notifier(**kwargs):
            sent_alerts.append(kwargs)

        worker = MonitoringWorker(meta_client=mock_meta, telegram_notifier=mock_notifier)
        stats = await worker.run_cycle()

        self.assertEqual(stats["adsets_reactivated"], 1)
        self.assertEqual(mock_meta.status_changes, [("adset_1", "ACTIVE")])
        self.assertNotIn("AUTO_REACTIVATE", [alert["event_type"] for alert in sent_alerts])

        async with self.test_session_maker() as session:
            stopped = (await session.execute(select(StoppedAdSet))).scalar_one()
            self.assertTrue(stopped.is_resolved)

    async def test_account_disabled_alert(self):
        """Если кабинет заблокирован в Meta → алерт ACCOUNT_ISSUE."""
        mock_meta = MockMetaClient()
        mock_meta.get_account_info = AsyncMock(return_value={
            "id": "act_e2e_sweden_1083",
            "name": "Underdog 3286",
            "account_status": 2, # Disabled
            "status_label": "Заблокирован в Meta (DISABLED / Policy Ban)",
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
