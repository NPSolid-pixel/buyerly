import unittest
from unittest.mock import AsyncMock

from meta_api.client import MetaClient


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


if __name__ == "__main__":
    unittest.main()
