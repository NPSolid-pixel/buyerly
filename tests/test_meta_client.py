import unittest
from unittest.mock import AsyncMock

from meta_api.client import MetaClient


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class TestMetaInsightsCollection(unittest.IsolatedAsyncioTestCase):
    async def test_account_summary_uses_unfiltered_account_level_insights(self):
        client = MetaClient()
        client._request_with_retry = AsyncMock(
            return_value=FakeResponse(
                {
                    "data": [
                        {
                            "spend": "742.35",
                            "impressions": "12000",
                            "clicks": "410",
                            "actions": [
                                {"action_type": "lead", "value": "52"},
                                {
                                    "action_type": "offsite_conversion.fb_pixel_lead",
                                    "value": "52",
                                },
                                {"action_type": "complete_registration", "value": "18"},
                                {"action_type": "purchase", "value": "4"},
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

        call = client._request_with_retry.await_args
        self.assertTrue(call.args[1].endswith("/act_123/insights"))
        self.assertEqual(call.kwargs["params"]["level"], "account")
        self.assertNotIn("filtering", call.kwargs["params"])
        self.assertNotIn("effective_status", call.kwargs["params"])

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
