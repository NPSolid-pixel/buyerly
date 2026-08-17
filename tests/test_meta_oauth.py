import unittest
from datetime import timezone
from urllib.parse import parse_qs, urlparse
from unittest.mock import AsyncMock

from meta_api.oauth import MetaOAuthClient, MetaOAuthRemoteError, meta_token_expiry


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class TestMetaOAuthClient(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = MetaOAuthClient(
            app_id="906676569173031",
            app_secret="app-secret",
            redirect_uri="https://smattrades.com/api/meta/oauth/callback",
            graph_version="v26.0",
            login_config_id="config-123",
        )

    async def test_authorization_url_uses_business_login_configuration(self):
        url = urlparse(self.client.build_authorization_url("one-time-state"))
        query = parse_qs(url.query)

        self.assertEqual(url.netloc, "www.facebook.com")
        self.assertEqual(url.path, "/v26.0/dialog/oauth")
        self.assertEqual(query["client_id"], ["906676569173031"])
        self.assertEqual(query["config_id"], ["config-123"])
        self.assertEqual(query["state"], ["one-time-state"])
        self.assertEqual(
            query["redirect_uri"],
            ["https://smattrades.com/api/meta/oauth/callback"],
        )

    async def test_debug_token_rejects_a_token_for_another_app(self):
        self.client._get_json = AsyncMock(
            return_value={"data": {"is_valid": True, "app_id": "other-app"}}
        )

        with self.assertRaises(MetaOAuthRemoteError):
            await self.client.debug_token("access-token")

    async def test_exchange_validates_and_returns_identity(self):
        self.client._get_json = AsyncMock(
            side_effect=[
                {"access_token": "short-token"},
                {"access_token": "long-token"},
                {
                    "data": {
                        "is_valid": True,
                        "app_id": "906676569173031",
                        "expires_at": 1_800_000_000,
                        "scopes": ["ads_read"],
                    }
                },
                {"id": "meta-user-1", "name": "Test User"},
            ]
        )

        result = await self.client.exchange_code("oauth-code")

        self.assertEqual(result["access_token"], "long-token")
        self.assertEqual(result["identity"]["id"], "meta-user-1")
        self.assertEqual(result["debug"]["scopes"], ["ads_read"])

    async def test_expiry_is_timezone_aware(self):
        expiry = meta_token_expiry({"expires_at": 1_800_000_000})

        self.assertIsNotNone(expiry)
        self.assertEqual(expiry.tzinfo, timezone.utc)

    async def test_account_discovery_collects_cursor_pages(self):
        self.client._get_json = AsyncMock(
            side_effect=[
                {
                    "data": [{"id": "act_1", "name": "First"}],
                    "paging": {
                        "next": "https://graph.facebook.com/next?access_token=secret",
                        "cursors": {"after": "cursor-1"},
                    },
                },
                {"data": [{"id": "act_2", "name": "Second"}]},
            ]
        )

        rows = await self.client.discover_ad_accounts("access-token")

        self.assertEqual([row["id"] for row in rows], ["act_1", "act_2"])
        second_call = self.client._get_json.await_args_list[1]
        self.assertEqual(second_call.kwargs["params"]["after"], "cursor-1")


if __name__ == "__main__":
    unittest.main()
