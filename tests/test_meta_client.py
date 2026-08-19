import hashlib
import hmac
import unittest
from unittest.mock import AsyncMock

import httpx

from core.config import settings
from meta_api.client import MetaClient, MetaRateLimitDeferred


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.text = ""

    def json(self):
        return self.payload


class TestMetaInsightsCollection(unittest.IsolatedAsyncioTestCase):
    async def test_graph_api_version_is_configurable_and_validated(self):
        client = MetaClient(graph_version="v25.0")

        self.assertEqual(client.graph_version, "v25.0")
        self.assertEqual(client.base_url, "https://graph.facebook.com/v25.0")

        with self.assertRaises(ValueError):
            MetaClient(graph_version="latest")

    async def test_account_info_normalizes_legacy_meta_timezone(self):
        client = MetaClient()
        client._request_with_retry = AsyncMock(
            return_value=FakeResponse(
                {
                    "id": "act_123",
                    "name": "Hawaii account",
                    "timezone_name": "US/Hawaii",
                    "currency": "USD",
                    "account_status": 1,
                }
            )
        )

        result = await client.get_account_info("act_123", "test-token")

        self.assertEqual(result["timezone_name"], "Pacific/Honolulu")
        call = client._request_with_retry.await_args
        self.assertIn("timezone_name", call.kwargs["params"]["fields"])

    async def test_live_adset_state_normalizes_meta_budget_units(self):
        client = MetaClient()
        client._request_with_retry = AsyncMock(
            return_value=FakeResponse(
                {
                    "id": "adset_1",
                    "name": "Test ad set",
                    "status": "PAUSED",
                    "effective_status": "CAMPAIGN_PAUSED",
                    "daily_budget": "12345",
                }
            )
        )

        state = await client.get_adset_state("adset_1", "test-token")

        self.assertEqual(state["status"], "PAUSED")
        self.assertEqual(state["effective_status"], "CAMPAIGN_PAUSED")
        self.assertEqual(state["daily_budget"], 123.45)
        call = client._request_with_retry.await_args
        self.assertIn("daily_budget", call.kwargs["params"]["fields"])

    async def test_zero_decimal_currency_budget_is_not_divided_or_multiplied_by_100(self):
        client = MetaClient()
        client._request_with_retry = AsyncMock(
            side_effect=[
                FakeResponse(
                    {
                        "id": "adset_jpy",
                        "status": "ACTIVE",
                        "daily_budget": "1200",
                    }
                ),
                FakeResponse({"success": True}),
            ]
        )

        state = await client.get_adset_state(
            "adset_jpy", "test-token", currency="JPY"
        )
        await client.update_adset_budget(
            "adset_jpy", "test-token", 1500, currency="JPY"
        )

        self.assertEqual(state["daily_budget"], 1200.0)
        budget_call = client._request_with_retry.await_args_list[1]
        self.assertEqual(budget_call.kwargs["data"]["daily_budget"], "1500")

    async def test_account_summary_uses_unfiltered_account_level_insights(self):
        client = MetaClient()
        client._request_with_retry = AsyncMock(
            return_value=FakeResponse(
                {
                    "data": [
                        {
                            "spend": "742.35",
                            "impressions": "12000",
                            "reach": "8000",
                            "frequency": "1.5",
                            "cpm": "61.8625",
                            "clicks": "410",
                            "unique_clicks": "360",
                            "inline_link_clicks": "280",
                            "outbound_clicks": [
                                {"action_type": "outbound_click", "value": "250"},
                            ],
                            "actions": [
                                {"action_type": "lead", "value": "52"},
                                {
                                    "action_type": "offsite_conversion.fb_pixel_lead",
                                    "value": "52",
                                },
                                {"action_type": "complete_registration", "value": "18"},
                                {"action_type": "purchase", "value": "4"},
                                {"action_type": "landing_page_view", "value": "230"},
                            ],
                        }
                    ]
                }
            )
        )

        result = await client.get_account_insights_summary(
            "act_123",
            "test-token",
            "today",
        )

        self.assertEqual(result["spend"], 742.35)
        self.assertEqual(result["leads"], 52)
        self.assertEqual(result["registrations"], 18)
        self.assertEqual(result["purchases"], 4)
        self.assertEqual(result["reach"], 8000)
        self.assertEqual(result["frequency"], 1.5)
        self.assertEqual(result["cpm"], 61.8625)
        self.assertEqual(result["clicks"], 410)
        self.assertEqual(result["unique_clicks"], 360)
        self.assertEqual(result["link_clicks"], 280)
        self.assertEqual(result["outbound_clicks"], 250)
        self.assertEqual(result["landing_page_views"], 230)

        call = client._request_with_retry.await_args
        self.assertTrue(call.args[1].endswith("/act_123/insights"))
        self.assertEqual(call.kwargs["params"]["level"], "account")
        self.assertNotIn("filtering", call.kwargs["params"])
        self.assertNotIn("effective_status", call.kwargs["params"])
        for field in (
            "reach",
            "frequency",
            "cpm",
            "unique_clicks",
            "inline_link_clicks",
            "outbound_clicks",
        ):
            self.assertIn(field, call.kwargs["params"]["fields"])

    async def test_delivery_metrics_are_derived_when_meta_omits_ratios(self):
        normalized = MetaClient._normalize_basic_insight(
            {
                "spend": "25",
                "impressions": "5000",
                "reach": "2500",
                "clicks": "0",
                "actions": [],
            }
        )

        self.assertEqual(normalized["frequency"], 2.0)
        self.assertEqual(normalized["cpm"], 5.0)
        self.assertEqual(normalized["link_clicks"], 0)
        self.assertEqual(normalized["landing_page_views"], 0)

    async def test_cursor_pagination_collects_every_page_without_following_next_url(self):
        client = MetaClient()
        client._request_with_retry = AsyncMock(
            side_effect=[
                FakeResponse(
                    {
                        "data": [{"id": "1"}],
                        "paging": {
                            "cursors": {"after": "cursor-1"},
                            "next": "https://graph.facebook.com/next?access_token=secret",
                        },
                    }
                ),
                FakeResponse({"data": [{"id": "2"}]}),
            ]
        )

        rows = await client._fetch_paginated_data(
            "https://graph.facebook.com/v20.0/act_123/adsets",
            {"limit": 100, "access_token": "test-token"},
            account_id="act_123",
        )

        self.assertEqual(rows, [{"id": "1"}, {"id": "2"}])
        second_call = client._request_with_retry.await_args_list[1]
        self.assertEqual(second_call.kwargs["params"]["after"], "cursor-1")
        self.assertEqual(
            second_call.args[1],
            "https://graph.facebook.com/v20.0/act_123/adsets",
        )

    async def test_requests_reuse_connection_and_add_appsecret_proof(self):
        seen_queries = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_queries.append(dict(request.url.params))
            return httpx.Response(200, json={"success": True})

        previous_secret = settings.META_APP_SECRET
        settings.META_APP_SECRET = "test-app-secret"
        client = MetaClient()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            original = {"access_token": "test-token", "fields": "id"}
            first_client = await client._get_client()
            await client._request_with_retry(
                "GET",
                f"{client.base_url}/act_1",
                params=original,
            )
            await client._request_with_retry(
                "GET",
                f"{client.base_url}/act_2",
                params=original,
            )
            second_client = await client._get_client()
        finally:
            await client.aclose()
            settings.META_APP_SECRET = previous_secret

        expected_proof = hmac.new(
            b"test-app-secret",
            b"test-token",
            hashlib.sha256,
        ).hexdigest()
        self.assertIs(first_client, second_client)
        self.assertEqual(len(seen_queries), 2)
        self.assertEqual(seen_queries[0]["appsecret_proof"], expected_proof)
        self.assertNotIn("appsecret_proof", original)

    async def test_inventory_is_cached_between_reporting_windows(self):
        client = MetaClient()
        client.configure_automation(inventory_cache_minutes=5)
        client._fetch_paginated_data = AsyncMock(
            side_effect=[
                [
                    {
                        "id": "adset_1",
                        "name": "Test",
                        "status": "ACTIVE",
                        "effective_status": "ACTIVE",
                        "daily_budget": "1000",
                    }
                ],
                [{"adset_id": "adset_1", "spend": "2"}],
                [{"adset_id": "adset_1", "spend": "3"}],
            ]
        )

        await client.get_adsets_insights("act_1", "token", "today", currency="USD")
        await client.get_adsets_insights("act_1", "token", "yesterday", currency="USD")

        urls = [call.args[0] for call in client._fetch_paginated_data.await_args_list]
        self.assertEqual(sum(url.endswith("/adsets") for url in urls), 1)
        self.assertEqual(sum(url.endswith("/insights") for url in urls), 2)

    async def test_hard_quota_defers_background_but_allows_critical_request(self):
        client = MetaClient()
        client.configure_automation(
            usage_soft_limit_percent=60,
            usage_hard_limit_percent=80,
        )
        client._usage_snapshot["max_percent"] = 85

        with self.assertRaises(MetaRateLimitDeferred):
            await client._respect_usage_limit(priority="normal")
        await client._respect_usage_limit(priority="critical")

    async def test_dead_archived_adsets_with_zero_activity_are_skipped(self):
        client = MetaClient()
        client._fetch_paginated_data = AsyncMock(
            side_effect=[
                [
                    {
                        "id": "adset_active",
                        "name": "Active Set",
                        "status": "ACTIVE",
                        "effective_status": "ACTIVE",
                        "daily_budget": "1000",
                    },
                    {
                        "id": "adset_dead_archived",
                        "name": "Old Archived Set",
                        "status": "ARCHIVED",
                        "effective_status": "ARCHIVED",
                        "daily_budget": "0",
                    },
                    {
                        "id": "adset_dead_deleted",
                        "name": "Old Deleted Set",
                        "status": "DELETED",
                        "effective_status": "DELETED",
                        "daily_budget": "0",
                    },
                ],
                [{"adset_id": "adset_active", "spend": "15.5", "impressions": "100", "clicks": "5"}],
            ]
        )

        results = await client.get_adsets_insights("act_1", "token", "today", currency="USD")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["adset_id"], "adset_active")
        self.assertEqual(results[0]["spend"], 15.5)

    async def test_archived_adset_with_spend_is_preserved(self):
        client = MetaClient()
        client._fetch_paginated_data = AsyncMock(
            side_effect=[
                [
                    {
                        "id": "adset_archived_today",
                        "name": "Archived Today",
                        "status": "ARCHIVED",
                        "effective_status": "ARCHIVED",
                        "daily_budget": "5000",
                    },
                ],
                [{"adset_id": "adset_archived_today", "spend": "42.0", "impressions": "500", "clicks": "20"}],
            ]
        )

        results = await client.get_adsets_insights("act_1", "token", "today", currency="USD")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["adset_id"], "adset_archived_today")
        self.assertEqual(results[0]["spend"], 42.0)

    async def test_orphan_insights_without_adset_inventory_are_included(self):
        client = MetaClient()
        client._fetch_paginated_data = AsyncMock(
            side_effect=[
                [],  # Empty adsets inventory
                [{"adset_id": "orphan_adset_99", "adset_name": "Deleted Midday", "spend": "25.0", "impressions": "300"}],
            ]
        )

        results = await client.get_adsets_insights("act_1", "token", "today", currency="USD")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["adset_id"], "orphan_adset_99")
        self.assertEqual(results[0]["adset_name"], "Deleted Midday")
        self.assertEqual(results[0]["spend"], 25.0)


if __name__ == "__main__":
    unittest.main()
