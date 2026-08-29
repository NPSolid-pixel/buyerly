import json
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import api.auth as api_auth_module
import api.routers.analytics as analytics_router_module
import api.routes as api_routes_module
import api.server as api_server_module
from api.server import create_app
from core.config import settings
from database.db import hash_password
from database.models import (
    Account,
    AnalyticsEntityFact,
    User,
    Workspace,
    WorkspaceMember,
)
from meta_api.client import MetaClient
from services.analytics_store import (
    AnalyticsFactService,
    resolve_account_period_dates,
)
from tests.test_api import generate_valid_telegram_init_data
from tests.test_db_helper import create_test_engine, init_test_db


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.text = ""

    def json(self):
        return self.payload


class TestAnalyticsFactStore(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        api_routes_module._summary_cache.clear()
        self.test_engine = create_test_engine()
        self.test_session_maker = async_sessionmaker(
            self.test_engine, class_=AsyncSession, expire_on_commit=False
        )
        await init_test_db(self.test_engine)

        api_routes_module.async_session_maker = self.test_session_maker
        api_auth_module.async_session_maker = self.test_session_maker
        analytics_router_module.async_session_maker = self.test_session_maker
        api_server_module.async_session_maker = self.test_session_maker

        settings.BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
        settings.ADMIN_CHAT_ID = "8634201356"

        async with self.test_session_maker() as session:
            # Workspace 1 & User 1
            self.user1 = User(
                telegram_id="11111111",
                username="buyer1",
                full_name="Buyer One",
                password_hash=hash_password("password123"),
                role="buyer",
                is_approved=True,
            )
            session.add(self.user1)
            await session.flush()

            self.ws1 = Workspace(
                name="Agency Alpha",
                slug="alpha",
                owner_user_id=self.user1.id,
            )
            session.add(self.ws1)
            await session.flush()

            self.user1.active_workspace_id = self.ws1.id
            session.add(
                WorkspaceMember(
                    workspace_id=self.ws1.id,
                    user_id=self.user1.id,
                    role="owner",
                )
            )

            # Account 1 in Workspace 1 (USD)
            self.acc1 = Account(
                workspace_id=self.ws1.id,
                owner_user_id=self.user1.id,
                account_id="act_1001",
                name="Alpha Main USD",
                currency="USD",
                timezone_name="America/New_York",
                is_active=True,
                account_status=1,
                status_label="Активен",
            )
            session.add(self.acc1)

            # Account 2 in Workspace 1 (EUR)
            self.acc2 = Account(
                workspace_id=self.ws1.id,
                owner_user_id=self.user1.id,
                account_id="act_1002",
                name="Alpha Euro EUR",
                currency="EUR",
                timezone_name="Europe/Berlin",
                is_active=True,
                account_status=1,
                status_label="Активен",
            )
            session.add(self.acc2)

            # Workspace 2 & User 2 (Isolated Tenant)
            self.user2 = User(
                telegram_id="22222222",
                username="buyer2",
                full_name="Buyer Two",
                password_hash=hash_password("password123"),
                role="buyer",
                is_approved=True,
            )
            session.add(self.user2)
            await session.flush()

            self.ws2 = Workspace(
                name="Agency Beta",
                slug="beta",
                owner_user_id=self.user2.id,
            )
            session.add(self.ws2)
            await session.flush()

            self.user2.active_workspace_id = self.ws2.id
            session.add(
                WorkspaceMember(
                    workspace_id=self.ws2.id,
                    user_id=self.user2.id,
                    role="owner",
                )
            )

            # Account 3 in Workspace 2
            self.acc3 = Account(
                workspace_id=self.ws2.id,
                owner_user_id=self.user2.id,
                account_id="act_2001",
                name="Beta Main USD",
                currency="USD",
                timezone_name="UTC",
                is_active=True,
                account_status=1,
                status_label="Активен",
            )
            session.add(self.acc3)

            await session.commit()

        self.app = create_app()

    async def test_resolve_account_period_dates(self):
        # Test timezone date resolution
        fixed_now = datetime(2026, 8, 29, 3, 0, 0, tzinfo=timezone.utc)
        # In New York (UTC-4), it is 2026-08-28 23:00:00
        dates_ny_today = resolve_account_period_dates("America/New_York", "today", now_utc=fixed_now)
        self.assertEqual(dates_ny_today, ["2026-08-28"])

        dates_ny_yesterday = resolve_account_period_dates("America/New_York", "yesterday", now_utc=fixed_now)
        self.assertEqual(dates_ny_yesterday, ["2026-08-27"])

        dates_ny_last_3d = resolve_account_period_dates("America/New_York", "last_3d", now_utc=fixed_now)
        self.assertEqual(dates_ny_last_3d, ["2026-08-28", "2026-08-27", "2026-08-26"])

        # In Berlin (UTC+2), it is 2026-08-29 05:00:00
        dates_berlin_today = resolve_account_period_dates("Europe/Berlin", "today", now_utc=fixed_now)
        self.assertEqual(dates_berlin_today, ["2026-08-29"])

    async def test_upsert_and_retrieve_facts_idempotency(self):
        async with self.test_session_maker() as session:
            today_str = datetime.now(timezone.utc).date().isoformat()
            raw_facts = [
                {
                    "entity_level": "account",
                    "entity_id": "act_1001",
                    "entity_name": "Alpha Main USD",
                    "parent_entity_id": "",
                    "date": today_str,
                    "currency": "USD",
                    "spend": 150.50,
                    "impressions": 10000,
                    "clicks": 500,
                    "leads": 25,
                    "registrations": 10,
                    "purchases": 5,
                },
                {
                    "entity_level": "campaign",
                    "entity_id": "cmp_1",
                    "entity_name": "Campaign 1",
                    "parent_entity_id": "act_1001",
                    "date": today_str,
                    "currency": "USD",
                    "spend": 100.00,
                    "impressions": 7000,
                    "clicks": 350,
                    "leads": 20,
                    "registrations": 8,
                    "purchases": 4,
                },
                {
                    "entity_level": "adset",
                    "entity_id": "adset_1",
                    "entity_name": "AdSet 1",
                    "parent_entity_id": "cmp_1",
                    "date": today_str,
                    "currency": "USD",
                    "spend": 60.00,
                    "impressions": 4000,
                    "clicks": 200,
                    "leads": 12,
                },
                {
                    "entity_level": "ad",
                    "entity_id": "ad_1",
                    "entity_name": "Ad 1",
                    "parent_entity_id": "adset_1",
                    "date": today_str,
                    "currency": "USD",
                    "spend": 30.00,
                    "impressions": 2000,
                    "clicks": 100,
                    "leads": 6,
                },
            ]

            count = await AnalyticsFactService.upsert_entity_facts(
                session=session,
                workspace_id=self.ws1.id,
                account_id="act_1001",
                facts=raw_facts,
            )
            await session.commit()
            self.assertEqual(count, 4)

            # Re-upserting updated metrics (idempotence)
            raw_facts[0]["spend"] = 200.00
            raw_facts[0]["leads"] = 30
            count2 = await AnalyticsFactService.upsert_entity_facts(
                session=session,
                workspace_id=self.ws1.id,
                account_id="act_1001",
                facts=raw_facts,
            )
            await session.commit()
            self.assertEqual(count2, 4)

            # Verify updated values in DB
            fact_row = (
                await session.execute(
                    select(AnalyticsEntityFact).where(
                        AnalyticsEntityFact.workspace_id == self.ws1.id,
                        AnalyticsEntityFact.entity_id == "act_1001",
                        AnalyticsEntityFact.date == today_str,
                    )
                )
            ).scalar_one()

            self.assertEqual(fact_row.spend, 200.00)
            self.assertEqual(fact_row.leads, 30)

    async def test_workspace_summary_multi_currency_and_division_safety(self):
        async with self.test_session_maker() as session:
            dates_acc1 = resolve_account_period_dates(self.acc1.timezone_name, "today")
            dates_acc2 = resolve_account_period_dates(self.acc2.timezone_name, "today")

            # Fact for Account 1 (USD, with 0 leads to test division by zero protection)
            await AnalyticsFactService.upsert_entity_facts(
                session,
                workspace_id=self.ws1.id,
                account_id=self.acc1.account_id,
                facts=[{
                    "entity_level": "account",
                    "entity_id": self.acc1.account_id,
                    "date": dates_acc1[0],
                    "currency": "USD",
                    "spend": 50.0,
                    "impressions": 2000,
                    "clicks": 100,
                    "leads": 0,  # Zero leads
                    "registrations": 0,
                    "purchases": 0,
                }],
            )

            # Fact for Account 2 (EUR, with 10 leads)
            await AnalyticsFactService.upsert_entity_facts(
                session,
                workspace_id=self.ws1.id,
                account_id=self.acc2.account_id,
                facts=[{
                    "entity_level": "account",
                    "entity_id": self.acc2.account_id,
                    "date": dates_acc2[0],
                    "currency": "EUR",
                    "spend": 100.0,
                    "impressions": 5000,
                    "clicks": 250,
                    "leads": 10,
                    "registrations": 5,
                    "purchases": 2,
                }],
            )
            await session.commit()

            summary = await AnalyticsFactService.get_workspace_summary_report(
                session,
                workspace_id=self.ws1.id,
                period="today",
                user_accounts=[self.acc1, self.acc2],
            )

            # Check mixed currency behavior (BL-015)
            self.assertTrue(summary["mixed_currencies"])
            self.assertEqual(summary["display_currency"], "")
            self.assertIsNone(summary["total_spend"])  # Mixed currencies must not be summed
            self.assertEqual(len(summary["currency_totals"]), 2)

            usd_bucket = next(b for b in summary["currency_totals"] if b["currency"] == "USD")
            eur_bucket = next(b for b in summary["currency_totals"] if b["currency"] == "EUR")

            self.assertEqual(usd_bucket["spend"], 50.0)
            self.assertIsNone(usd_bucket["cost_per_lead"])  # Division by zero safety!

            self.assertEqual(eur_bucket["spend"], 100.0)
            self.assertEqual(eur_bucket["leads"], 10)
            self.assertEqual(eur_bucket["cost_per_lead"], 10.0)

    async def test_hierarchical_drill_down_and_tenant_isolation(self):
        async with self.test_session_maker() as session:
            today_str = datetime.now(timezone.utc).date().isoformat()

            # Insert hierarchy into Workspace 1
            await AnalyticsFactService.upsert_entity_facts(
                session,
                workspace_id=self.ws1.id,
                account_id=self.acc1.account_id,
                facts=[
                    {
                        "entity_level": "campaign",
                        "entity_id": "cmp_alpha_1",
                        "entity_name": "Alpha Campaign 1",
                        "parent_entity_id": self.acc1.account_id,
                        "date": today_str,
                        "currency": "USD",
                        "spend": 80.0,
                        "impressions": 4000,
                        "clicks": 200,
                        "leads": 10,
                    },
                    {
                        "entity_level": "adset",
                        "entity_id": "adset_alpha_1",
                        "entity_name": "Alpha Adset 1",
                        "parent_entity_id": "cmp_alpha_1",
                        "date": today_str,
                        "currency": "USD",
                        "spend": 80.0,
                        "impressions": 4000,
                        "clicks": 200,
                        "leads": 10,
                    },
                ],
            )

            # Insert hierarchy into Workspace 2
            await AnalyticsFactService.upsert_entity_facts(
                session,
                workspace_id=self.ws2.id,
                account_id=self.acc3.account_id,
                facts=[
                    {
                        "entity_level": "campaign",
                        "entity_id": "cmp_beta_1",
                        "entity_name": "Beta Secret Campaign",
                        "parent_entity_id": self.acc3.account_id,
                        "date": today_str,
                        "currency": "USD",
                        "spend": 500.0,
                        "impressions": 20000,
                        "clicks": 1000,
                        "leads": 50,
                    }
                ],
            )
            await session.commit()

            # Query campaigns for Workspace 1
            breakdown_w1 = await AnalyticsFactService.get_hierarchy_breakdown(
                session,
                workspace_id=self.ws1.id,
                parent_entity_id=self.acc1.account_id,
                entity_level="campaign",
                period="today",
            )
            self.assertEqual(len(breakdown_w1), 1)
            self.assertEqual(breakdown_w1[0]["entity_id"], "cmp_alpha_1")
            self.assertEqual(breakdown_w1[0]["cost_per_lead"], 8.0)

            # Verify Tenant Isolation: Workspace 1 query MUST NOT see Workspace 2 campaigns
            breakdown_leak_attempt = await AnalyticsFactService.get_hierarchy_breakdown(
                session,
                workspace_id=self.ws1.id,
                parent_entity_id=self.acc3.account_id,
                entity_level="campaign",
                period="today",
            )
            self.assertEqual(len(breakdown_leak_attempt), 0)

    async def test_meta_client_get_hierarchical_insights(self):
        client = MetaClient()
        client._fetch_paginated_data = AsyncMock(
            side_effect=[
                # 1. Account insights summary
                [{"spend": "100.00", "impressions": "5000", "clicks": "200", "actions": [{"action_type": "lead", "value": "10"}]}],
                # 2. Campaign level insights
                [{"campaign_id": "c1", "campaign_name": "Camp 1", "spend": "60.00", "impressions": "3000", "clicks": "120", "actions": [{"action_type": "lead", "value": "6"}]}],
                # 3. Adset level insights
                [{"adset_id": "as1", "adset_name": "AdSet 1", "campaign_id": "c1", "spend": "40.00", "impressions": "2000", "clicks": "80", "actions": []}],
                # 4. Ad level insights
                [{"ad_id": "ad1", "ad_name": "Ad 1", "adset_id": "as1", "spend": "20.00", "impressions": "1000", "clicks": "40", "actions": []}],
            ]
        )

        facts = await client.get_hierarchical_insights(
            account_id="act_1001",
            access_token="test_token",
            date_preset="today",
            currency="USD",
            account_name="Alpha USD",
        )

        self.assertEqual(len(facts), 4)
        levels = [f["entity_level"] for f in facts]
        self.assertEqual(levels, ["account", "campaign", "adset", "ad"])
        self.assertEqual(facts[0]["spend"], 100.0)
        self.assertEqual(facts[0]["leads"], 10)
        self.assertEqual(facts[1]["entity_id"], "c1")
        self.assertEqual(facts[1]["parent_entity_id"], "act_1001")
        self.assertEqual(facts[2]["entity_id"], "as1")
        self.assertEqual(facts[2]["parent_entity_id"], "c1")
        self.assertEqual(facts[3]["entity_id"], "ad1")
        self.assertEqual(facts[3]["parent_entity_id"], "as1")

    async def test_analytics_hierarchy_api_endpoint(self):
        async with self.test_session_maker() as session:
            today_str = resolve_account_period_dates(self.acc1.timezone_name, "today")[0]
            await AnalyticsFactService.upsert_entity_facts(
                session,
                workspace_id=self.ws1.id,
                account_id=self.acc1.account_id,
                facts=[{
                    "entity_level": "campaign",
                    "entity_id": "cmp_api_1",
                    "entity_name": "Campaign API Test",
                    "parent_entity_id": self.acc1.account_id,
                    "date": today_str,
                    "currency": "USD",
                    "spend": 75.0,
                    "impressions": 3000,
                    "clicks": 150,
                    "leads": 5,
                }],
            )
            await session.commit()

        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            init_data_w1 = generate_valid_telegram_init_data(
                settings.BOT_TOKEN,
                {"id": 11111111, "first_name": "Buyer One", "username": "buyer1"},
            )
            headers_w1 = {"Authorization": f"tma {init_data_w1}"}

            # Authorized query for own account's campaigns
            res = await ac.get(
                f"/api/analytics/hierarchy?parent_id={self.acc1.account_id}&level=campaign&period=today",
                headers=headers_w1,
            )
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data["total"], 1)
            self.assertEqual(data["items"][0]["entity_id"], "cmp_api_1")
            self.assertEqual(data["items"][0]["spend"], 75.0)

            # Query for alien account from another workspace should be rejected (404)
            res_alien = await ac.get(
                f"/api/analytics/hierarchy?parent_id={self.acc3.account_id}&level=campaign&period=today",
                headers=headers_w1,
            )
            self.assertEqual(res_alien.status_code, 404)

    async def test_retention_cleanup(self):
        async with self.test_session_maker() as session:
            old_date = (datetime.now(timezone.utc).date() - timedelta(days=70)).isoformat()
            await AnalyticsFactService.upsert_entity_facts(
                session,
                workspace_id=self.ws1.id,
                account_id=self.acc1.account_id,
                facts=[{
                    "entity_level": "ad",
                    "entity_id": "ad_old",
                    "entity_name": "Old Ad",
                    "parent_entity_id": "adset_1",
                    "date": old_date,
                    "currency": "USD",
                    "spend": 10.0,
                }],
            )
            await session.commit()

            deleted = await AnalyticsFactService.cleanup_expired_facts(
                session,
                ad_days=60,
            )
            self.assertGreaterEqual(deleted, 1)

            # Check that old ad was purged
            remaining = (
                await session.execute(
                    select(AnalyticsEntityFact).where(AnalyticsEntityFact.entity_id == "ad_old")
                )
            ).scalars().all()
            self.assertEqual(len(remaining), 0)
