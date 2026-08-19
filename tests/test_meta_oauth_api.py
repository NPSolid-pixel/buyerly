import unittest
from unittest.mock import AsyncMock, patch

import httpx
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import api.auth as auth_module
import api.meta_oauth as meta_oauth_module
import api.server as server_module
from api.server import create_app
from core.config import settings
from core.meta_tokens import encrypt_meta_token
from database.db import Base
from database.models import Account, MetaConnection, TelegramUser


class TestMetaOAuthApi(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.sessions = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        auth_module.async_session_maker = self.sessions
        meta_oauth_module.async_session_maker = self.sessions
        server_module.async_session_maker = self.sessions
        self.original_settings = {
            key: getattr(settings, key)
            for key in (
                "META_APP_ID",
                "META_APP_SECRET",
                "META_LOGIN_CONFIG_ID",
                "META_OAUTH_REDIRECT_URI",
                "META_TOKEN_ENCRYPTION_KEY",
                "WEBAPP_URL",
            )
        }
        settings.META_APP_ID = "906676569173031"
        settings.META_APP_SECRET = "test-app-secret"
        settings.META_LOGIN_CONFIG_ID = "config-123"
        settings.META_OAUTH_REDIRECT_URI = "https://buyerly.app/api/meta/oauth/callback"
        settings.META_TOKEN_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")
        settings.WEBAPP_URL = "https://buyerly.app"

        async with self.sessions() as session:
            user = TelegramUser(
                telegram_id="10001",
                username="oauth-owner",
                full_name="OAuth Owner",
                auth_token="buyerly-test-token",
                role="buyer",
                is_approved=True,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            self.user_id = user.id
            connection = MetaConnection(
                owner_id=user.telegram_id,
                owner_user_id=user.id,
                provider_user_id="meta-user-1",
                provider_user_name="Meta Test User",
                access_token_encrypted=encrypt_meta_token("EAAB-test-token"),
                status="active",
            )
            session.add(connection)
            await session.commit()
            await session.refresh(connection)
            self.connection_id = connection.id

        self.app = create_app()
        self.headers = {"Authorization": "Bearer buyerly-test-token"}

    async def asyncTearDown(self):
        for key, value in self.original_settings.items():
            setattr(settings, key, value)
        await self.engine.dispose()

    async def test_start_returns_business_login_url_and_persists_state(self):
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/meta/oauth/start?return_path=/add-accounts",
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("www.facebook.com/v26.0/dialog/oauth", payload["authorization_url"])
        self.assertIn("config_id=config-123", payload["authorization_url"])
        self.assertNotIn("test-app-secret", payload["authorization_url"])

    async def test_discover_and_import_keep_token_out_of_account_row(self):
        fake_oauth = AsyncMock()
        fake_oauth.debug_token.return_value = {
            "is_valid": True,
            "app_id": settings.META_APP_ID,
            "scopes": ["ads_read", "ads_management", "business_management"],
        }
        fake_oauth.discover_ad_accounts.return_value = [
            {
                "id": "act_123456789",
                "name": "Pilot account",
                "account_status": 1,
                "currency": "USD",
                "timezone_name": "US/Hawaii",
                "business": {"id": "bm-1", "name": "Pilot BM"},
            }
        ]
        account_info = {
            "id": "act_123456789",
            "name": "Pilot account",
            "account_status": 1,
            "currency": "USD",
            "timezone_name": "Pacific/Honolulu",
            "status_label": "🟢 Активен (ACTIVE)",
        }

        transport = httpx.ASGITransport(app=self.app)
        with (
            patch.object(meta_oauth_module, "_oauth_client", return_value=fake_oauth),
            patch.object(
                meta_oauth_module.meta_client,
                "get_account_info",
                new=AsyncMock(return_value=account_info),
            ),
        ):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                discovery = await client.post(
                    f"/api/meta/connections/{self.connection_id}/discover",
                    headers=self.headers,
                )
                imported = await client.post(
                    f"/api/meta/connections/{self.connection_id}/import",
                    headers=self.headers,
                    json={"account_ids": ["act_123456789"]},
                )

        self.assertEqual(discovery.status_code, 200)
        self.assertEqual(discovery.json()["accounts"][0]["business_name"], "Pilot BM")
        self.assertFalse(discovery.json()["accounts"][0]["imported"])
        self.assertEqual(imported.status_code, 200)
        self.assertEqual(imported.json()["success_count"], 1)

        async with self.sessions() as session:
            account = (
                await session.execute(
                    select(Account).where(Account.account_id == "act_123456789")
                )
            ).scalar_one()
            self.assertEqual(account.access_token, "")
            self.assertEqual(account.meta_connection_id, self.connection_id)
            self.assertFalse(account.rules_enabled)
            self.assertEqual(account.timezone_name, "Pacific/Honolulu")


if __name__ == "__main__":
    unittest.main()
