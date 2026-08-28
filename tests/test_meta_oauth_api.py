import json
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
from database.models import (
    Account,
    AuditEvent,
    MetaConnection,
    User,
    Workspace,
    WorkspaceMember,
)


from tests.test_db_helper import create_test_engine, init_test_db


class TestMetaOAuthApi(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_test_engine()
        self.sessions = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        await init_test_db(self.engine)

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
            user = User(
                telegram_id="10001",
                username="oauth-owner",
                full_name="OAuth Owner",
                auth_token="buyerly-test-token",
                role="buyer",
                is_approved=True,
            )
            session.add(user)
            await session.flush()
            workspace = Workspace(
                name="OAuth workspace",
                slug="oauth-workspace",
                owner_user_id=user.id,
            )
            session.add(workspace)
            await session.flush()
            session.add(
                WorkspaceMember(
                    workspace_id=workspace.id,
                    user_id=user.id,
                    role="owner",
                )
            )
            user.active_workspace_id = workspace.id
            self.user_id = user.id
            self.workspace_id = workspace.id
            connection = MetaConnection(
                workspace_id=workspace.id,
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
                "/api/meta/oauth/start?return_path=/facebook-accounts",
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
            "status_label": "Активен (ACTIVE)",
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
            connection = await session.get(MetaConnection, self.connection_id)
            self.assertEqual(account.access_token, "")
            self.assertEqual(account.meta_connection_id, self.connection_id)
            self.assertFalse(account.rules_enabled)
            self.assertEqual(account.timezone_name, "Pacific/Honolulu")
            self.assertEqual(
                connection.granted_scopes,
                ["ads_read", "ads_management", "business_management"],
            )

    async def test_delete_connection_disables_linked_accounts_and_writes_audit(self):
        async with self.sessions() as session:
            account = Account(
                account_id="act_987654321",
                name="Linked account",
                workspace_id=self.workspace_id,
                owner_user_id=self.user_id,
                meta_connection_id=self.connection_id,
                access_token="",
                currency="USD",
                rules_enabled=True,
                is_active=True,
            )
            session.add(account)
            await session.commit()

        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.delete(
                f"/api/meta/connections/{self.connection_id}",
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["detached_account_count"], 1)
        async with self.sessions() as session:
            connection = await session.get(MetaConnection, self.connection_id)
            account = (
                await session.execute(
                    select(Account).where(Account.account_id == "act_987654321")
                )
            ).scalar_one()
            audit = (
                await session.execute(
                    select(AuditEvent).where(
                        AuditEvent.event_type == "META_CONNECTION_DISCONNECTED"
                    )
                )
            ).scalar_one()

        self.assertIsNone(connection)
        self.assertIsNone(account.meta_connection_id)
        self.assertFalse(account.rules_enabled)
        self.assertFalse(account.is_active)
        self.assertEqual(audit.workspace_id, self.workspace_id)
        self.assertEqual(audit.details["account_ids"], ["act_987654321"])

    async def test_delete_connection_blocks_viewer_and_foreign_workspace(self):
        async with self.sessions() as session:
            viewer = User(
                telegram_id="10002",
                username="oauth-viewer",
                auth_token="oauth-viewer-token",
                role="buyer",
                is_approved=True,
                active_workspace_id=self.workspace_id,
            )
            session.add(viewer)
            await session.flush()
            session.add(
                WorkspaceMember(
                    workspace_id=self.workspace_id,
                    user_id=viewer.id,
                    role="viewer",
                )
            )
            second_workspace = Workspace(
                name="Second OAuth workspace",
                slug="second-oauth-workspace",
                owner_user_id=self.user_id,
            )
            session.add(second_workspace)
            await session.flush()
            session.add(
                WorkspaceMember(
                    workspace_id=second_workspace.id,
                    user_id=self.user_id,
                    role="owner",
                )
            )
            foreign_connection = MetaConnection(
                workspace_id=second_workspace.id,
                owner_user_id=self.user_id,
                provider_user_id="meta-user-foreign",
                provider_user_name="Foreign workspace Meta user",
                access_token_encrypted=encrypt_meta_token("EAAB-foreign-token"),
                status="active",
            )
            session.add(foreign_connection)
            await session.commit()
            foreign_connection_id = foreign_connection.id

        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            viewer_response = await client.delete(
                f"/api/meta/connections/{self.connection_id}",
                headers={"Authorization": "Bearer oauth-viewer-token"},
            )
            foreign_response = await client.delete(
                f"/api/meta/connections/{foreign_connection_id}",
                headers=self.headers,
            )

        self.assertEqual(viewer_response.status_code, 403)
        self.assertEqual(foreign_response.status_code, 404)
        async with self.sessions() as session:
            self.assertIsNotNone(await session.get(MetaConnection, self.connection_id))
            self.assertIsNotNone(await session.get(MetaConnection, foreign_connection_id))

    async def test_validate_connection_returns_health_status(self):
        fake_oauth = AsyncMock()
        fake_oauth.debug_token.return_value = {
            "is_valid": True,
            "app_id": settings.META_APP_ID,
            "scopes": ["ads_read", "ads_management", "business_management"],
            "expires_at": 1893456000,
        }

        transport = httpx.ASGITransport(app=self.app)
        with patch.object(meta_oauth_module, "_oauth_client", return_value=fake_oauth):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/api/meta/connections/{self.connection_id}/validate",
                    headers=self.headers,
                )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "active")
        self.assertEqual(data["missing_scopes"], [])
        self.assertIn("ads_management", data["granted_scopes"])
        self.assertIsNotNone(data["days_until_expiration"])
        self.assertIsNotNone(data["last_validated_at"])

    async def test_validate_connection_detects_missing_scopes(self):
        fake_oauth = AsyncMock()
        fake_oauth.debug_token.return_value = {
            "is_valid": True,
            "app_id": settings.META_APP_ID,
            "scopes": ["ads_read"],  # missing ads_management and business_management
            "expires_at": 1893456000,
        }

        transport = httpx.ASGITransport(app=self.app)
        with patch.object(meta_oauth_module, "_oauth_client", return_value=fake_oauth):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/api/meta/connections/{self.connection_id}/validate",
                    headers=self.headers,
                )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "missing_scopes")
        self.assertIn("ads_management", data["missing_scopes"])
        self.assertIn("business_management", data["missing_scopes"])

    async def test_reconnect_start_and_callback_flow(self):
        async with self.sessions() as session:
            account = Account(
                account_id="act_555666777",
                name="Inactive Linked Account",
                workspace_id=self.workspace_id,
                owner_user_id=self.user_id,
                meta_connection_id=self.connection_id,
                access_token="",
                currency="USD",
                rules_enabled=False,
                is_active=False,
                status_label="Требуется подключение Meta",
            )
            session.add(account)
            await session.commit()

        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            start_resp = await client.post(
                f"/api/meta/oauth/start?return_path=/facebook-accounts&reconnect_connection_id={self.connection_id}",
                headers=self.headers,
            )
            self.assertEqual(start_resp.status_code, 200)

            fake_oauth = AsyncMock()
            fake_oauth.exchange_code.return_value = {
                "access_token": "EAAB-new-reconnected-token",
                "identity": {"id": "meta-user-1", "name": "Meta Test User (Updated)"},
                "debug": {
                    "is_valid": True,
                    "app_id": settings.META_APP_ID,
                    "scopes": ["ads_read", "ads_management", "business_management"],
                    "expires_at": 1893456000,
                },
            }

            from urllib.parse import parse_qs, urlparse
            auth_url = start_resp.json()["authorization_url"]
            state = parse_qs(urlparse(auth_url).query)["state"][0]

            with patch.object(meta_oauth_module, "_oauth_client", return_value=fake_oauth):
                cb_resp = await client.get(
                    f"/api/meta/oauth/callback?state={state}&code=test-auth-code",
                    follow_redirects=False,
                )

        self.assertEqual(cb_resp.status_code, 303)
        self.assertIn("meta_status=connected", cb_resp.headers["location"])

        async with self.sessions() as session:
            conn = await session.get(MetaConnection, self.connection_id)
            self.assertEqual(conn.provider_user_name, "Meta Test User (Updated)")
            self.assertEqual(conn.status, "active")

            acc = (
                await session.execute(
                    select(Account).where(Account.account_id == "act_555666777")
                )
            ).scalar_one()
            self.assertTrue(acc.is_active)
            self.assertEqual(acc.status_label, "Активен")

            audit = (
                await session.execute(
                    select(AuditEvent).where(
                        AuditEvent.event_type == "META_CONNECTION_RECONNECTED"
                    )
                )
            ).scalar_one()
            self.assertEqual(audit.action, "RECONNECT_CONNECTION")

    async def test_reconnect_blocks_identity_mismatch(self):
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            start_resp = await client.post(
                f"/api/meta/oauth/start?return_path=/facebook-accounts&reconnect_connection_id={self.connection_id}",
                headers=self.headers,
            )
            self.assertEqual(start_resp.status_code, 200)

            fake_oauth = AsyncMock()
            fake_oauth.exchange_code.return_value = {
                "access_token": "EAAB-different-token",
                "identity": {"id": "meta-user-DIFFERENT-ID", "name": "Different Meta User"},
                "debug": {
                    "is_valid": True,
                    "app_id": settings.META_APP_ID,
                    "scopes": ["ads_read", "ads_management", "business_management"],
                },
            }

            from urllib.parse import parse_qs, urlparse
            auth_url = start_resp.json()["authorization_url"]
            state = parse_qs(urlparse(auth_url).query)["state"][0]

            with patch.object(meta_oauth_module, "_oauth_client", return_value=fake_oauth):
                cb_resp = await client.get(
                    f"/api/meta/oauth/callback?state={state}&code=test-auth-code",
                    follow_redirects=False,
                )

        self.assertEqual(cb_resp.status_code, 303)
        self.assertIn("meta_status=identity_mismatch", cb_resp.headers["location"])

    async def test_multi_workspace_same_fb_profile_independent_connections(self):
        async with self.sessions() as session:
            ws2 = Workspace(
                name="Second Team",
                slug="second-team",
                owner_user_id=self.user_id,
            )
            session.add(ws2)
            await session.flush()
            session.add(
                WorkspaceMember(
                    workspace_id=ws2.id,
                    user_id=self.user_id,
                    role="owner",
                )
            )
            user = await session.get(User, self.user_id)
            user.active_workspace_id = ws2.id
            await session.commit()
            ws2_id = ws2.id

        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Start OAuth in workspace 2
            start_resp = await client.post(
                "/api/meta/oauth/start?return_path=/facebook-accounts",
                headers=self.headers,
            )
            self.assertEqual(start_resp.status_code, 200)

            fake_oauth = AsyncMock()
            fake_oauth.exchange_code.return_value = {
                "access_token": "EAAB-ws2-token",
                "identity": {"id": "meta-user-1", "name": "Meta Test User"},
                "debug": {
                    "is_valid": True,
                    "app_id": settings.META_APP_ID,
                    "scopes": ["ads_read", "ads_management", "business_management"],
                    "expires_at": 1893456000,
                },
            }

            from urllib.parse import parse_qs, urlparse
            auth_url = start_resp.json()["authorization_url"]
            state = parse_qs(urlparse(auth_url).query)["state"][0]

            with patch.object(meta_oauth_module, "_oauth_client", return_value=fake_oauth):
                cb_resp = await client.get(
                    f"/api/meta/oauth/callback?state={state}&code=test-code-ws2",
                    follow_redirects=False,
                )

        self.assertEqual(cb_resp.status_code, 303)
        self.assertIn("meta_status=connected", cb_resp.headers["location"])

        async with self.sessions() as session:
            conn_ws1 = (
                await session.execute(
                    select(MetaConnection).where(
                        MetaConnection.workspace_id == self.workspace_id,
                        MetaConnection.provider_user_id == "meta-user-1",
                    )
                )
            ).scalar_one()
            conn_ws2 = (
                await session.execute(
                    select(MetaConnection).where(
                        MetaConnection.workspace_id == ws2_id,
                        MetaConnection.provider_user_id == "meta-user-1",
                    )
                )
            ).scalar_one()

            self.assertNotEqual(conn_ws1.id, conn_ws2.id)
            self.assertEqual(conn_ws1.workspace_id, self.workspace_id)
            self.assertEqual(conn_ws2.workspace_id, ws2_id)

    async def test_oauth_callback_preserves_initial_workspace_even_if_active_workspace_switched(self):
        async with self.sessions() as session:
            ws_other = Workspace(
                name="Other WS",
                slug="other-ws",
                owner_user_id=self.user_id,
            )
            session.add(ws_other)
            await session.flush()
            session.add(
                WorkspaceMember(
                    workspace_id=ws_other.id,
                    user_id=self.user_id,
                    role="owner",
                )
            )
            await session.commit()
            other_ws_id = ws_other.id

        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Start OAuth while self.workspace_id is active
            start_resp = await client.post(
                "/api/meta/oauth/start?return_path=/facebook-accounts",
                headers=self.headers,
            )
            self.assertEqual(start_resp.status_code, 200)

            # User switches active workspace to other_ws_id in another tab
            async with self.sessions() as session:
                user = await session.get(User, self.user_id)
                user.active_workspace_id = other_ws_id
                await session.commit()

            fake_oauth = AsyncMock()
            fake_oauth.exchange_code.return_value = {
                "access_token": "EAAB-preserved-token",
                "identity": {"id": "meta-user-new-profile", "name": "New Profile"},
                "debug": {
                    "is_valid": True,
                    "app_id": settings.META_APP_ID,
                    "scopes": ["ads_read", "ads_management", "business_management"],
                    "expires_at": 1893456000,
                },
            }

            from urllib.parse import parse_qs, urlparse
            auth_url = start_resp.json()["authorization_url"]
            state = parse_qs(urlparse(auth_url).query)["state"][0]

            with patch.object(meta_oauth_module, "_oauth_client", return_value=fake_oauth):
                cb_resp = await client.get(
                    f"/api/meta/oauth/callback?state={state}&code=test-code-switch",
                    follow_redirects=False,
                )

        self.assertEqual(cb_resp.status_code, 303)
        self.assertIn("meta_status=connected", cb_resp.headers["location"])

        async with self.sessions() as session:
            # Connection MUST belong to self.workspace_id (the initial workspace where start was invoked)
            conn = (
                await session.execute(
                    select(MetaConnection).where(
                        MetaConnection.provider_user_id == "meta-user-new-profile"
                    )
                )
            ).scalar_one()
            self.assertEqual(conn.workspace_id, self.workspace_id)
            self.assertNotEqual(conn.workspace_id, other_ws_id)

    async def test_oauth_start_blocks_viewer_role(self):
        async with self.sessions() as session:
            viewer = User(
                username="pure-viewer",
                auth_token="pure-viewer-token",
                role="buyer",
                is_approved=True,
                active_workspace_id=self.workspace_id,
            )
            session.add(viewer)
            await session.flush()
            session.add(
                WorkspaceMember(
                    workspace_id=self.workspace_id,
                    user_id=viewer.id,
                    role="viewer",
                )
            )
            await session.commit()

        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/meta/oauth/start",
                headers={"Authorization": "Bearer pure-viewer-token"},
            )
            self.assertEqual(resp.status_code, 403)

    async def test_oauth_callback_blocks_revoked_or_viewer_membership(self):
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            start_resp = await client.post(
                "/api/meta/oauth/start?return_path=/facebook-accounts",
                headers=self.headers,
            )
            self.assertEqual(start_resp.status_code, 200)

            # Demote member to viewer before callback arrives
            async with self.sessions() as session:
                member = (
                    await session.execute(
                        select(WorkspaceMember).where(
                            WorkspaceMember.workspace_id == self.workspace_id,
                            WorkspaceMember.user_id == self.user_id,
                        )
                    )
                ).scalar_one()
                member.role = "viewer"
                await session.commit()

            fake_oauth = AsyncMock()
            fake_oauth.exchange_code.return_value = {
                "access_token": "EAAB-demoted-token",
                "identity": {"id": "meta-user-demoted", "name": "Demoted"},
                "debug": {
                    "is_valid": True,
                    "app_id": settings.META_APP_ID,
                    "scopes": ["ads_read", "ads_management", "business_management"],
                },
            }

            from urllib.parse import parse_qs, urlparse
            auth_url = start_resp.json()["authorization_url"]
            state = parse_qs(urlparse(auth_url).query)["state"][0]

            with patch.object(meta_oauth_module, "_oauth_client", return_value=fake_oauth):
                cb_resp = await client.get(
                    f"/api/meta/oauth/callback?state={state}&code=test-code-demote",
                    follow_redirects=False,
                )

        self.assertEqual(cb_resp.status_code, 303)
        self.assertIn("meta_status=permission_denied", cb_resp.headers["location"])

    async def test_discover_assets_distinguishes_manual_token_accounts_and_rules_count(self):
        async with self.sessions() as session:
            manual_acc = Account(
                account_id="act_555000",
                name="Legacy Manual Account",
                custom_name="Buyerly Custom Name",
                note="Existing secret notes",
                workspace_id=self.workspace_id,
                owner_user_id=self.user_id,
                access_token_encrypted=encrypt_meta_token("EAAB-manual-555"),
                meta_connection_id=None,
                active_rules=json.dumps([{"preset_id": 1, "name": "Stop on high CPL"}]),
                rules_enabled=True,
                is_active=True,
            )
            session.add(manual_acc)
            await session.commit()

        fake_oauth = AsyncMock()
        fake_oauth.debug_token.return_value = {
            "is_valid": True,
            "app_id": settings.META_APP_ID,
            "scopes": ["ads_read", "ads_management", "business_management"],
        }
        fake_oauth.discover_ad_accounts.return_value = [
            {
                "id": "act_555000",
                "name": "Legacy Manual Account",
                "account_status": 1,
                "currency": "USD",
                "timezone_name": "US/Hawaii",
                "business": {"id": "bm-1", "name": "Main BM"},
            },
            {
                "id": "act_999000",
                "name": "Brand New Account",
                "account_status": 1,
                "currency": "EUR",
                "timezone_name": "UTC",
                "business": {"id": "bm-1", "name": "Main BM"},
            },
        ]

        transport = httpx.ASGITransport(app=self.app)
        with patch.object(meta_oauth_module, "_oauth_client", return_value=fake_oauth):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                discovery = await client.post(
                    f"/api/meta/connections/{self.connection_id}/discover",
                    headers=self.headers,
                )

        self.assertEqual(discovery.status_code, 200)
        data = discovery.json()
        self.assertEqual(data["count"], 2)
        self.assertEqual(data["migratable_count"], 1)
        self.assertEqual(data["imported_count"], 0)

        accounts_by_id = {acc["account_id"]: acc for acc in data["accounts"]}
        manual_item = accounts_by_id["act_555000"]
        self.assertEqual(manual_item["import_status"], "manual_token")
        self.assertTrue(manual_item["can_migrate"])
        self.assertFalse(manual_item["imported"])  # Not disabled in UI
        self.assertEqual(manual_item["rules_count"], 1)
        self.assertTrue(manual_item["rules_enabled"])
        self.assertEqual(manual_item["custom_name"], "Buyerly Custom Name")

        new_item = accounts_by_id["act_999000"]
        self.assertEqual(new_item["import_status"], "not_imported")
        self.assertFalse(new_item["can_migrate"])
        self.assertFalse(new_item["imported"])
        self.assertEqual(new_item["rules_count"], 0)

    async def test_import_migrates_manual_account_to_oauth_preserving_rules_and_state(self):
        async with self.sessions() as session:
            manual_acc = Account(
                account_id="act_777000",
                name="Legacy Manual 777",
                custom_name="Custom 777",
                note="Crucial note",
                workspace_id=self.workspace_id,
                owner_user_id=self.user_id,
                access_token_encrypted=encrypt_meta_token("EAAB-manual-777"),
                meta_connection_id=None,
                active_rules=json.dumps([{"preset_id": 10, "name": "Auto Pause 10"}]),
                rules_enabled=True,
                is_active=True,
            )
            session.add(manual_acc)
            await session.commit()

        fake_oauth = AsyncMock()
        fake_oauth.debug_token.return_value = {
            "is_valid": True,
            "app_id": settings.META_APP_ID,
            "scopes": ["ads_read", "ads_management", "business_management"],
        }
        fake_oauth.discover_ad_accounts.return_value = [
            {
                "id": "act_777000",
                "name": "Legacy Manual 777",
                "account_status": 1,
                "currency": "USD",
                "timezone_name": "US/Pacific",
                "business": {"id": "bm-777", "name": "BM 777"},
            }
        ]
        account_info = {
            "id": "act_777000",
            "name": "Legacy Manual 777 (Meta)",
            "account_status": 1,
            "currency": "USD",
            "timezone_name": "America/Los_Angeles",
            "status_label": "Активен (ACTIVE)",
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
                await client.post(
                    f"/api/meta/connections/{self.connection_id}/discover",
                    headers=self.headers,
                )
                import_resp = await client.post(
                    f"/api/meta/connections/{self.connection_id}/import",
                    headers=self.headers,
                    json={"account_ids": ["act_777000"]},
                )

        self.assertEqual(import_resp.status_code, 200)
        resp_data = import_resp.json()
        self.assertEqual(resp_data["success_count"], 1)
        added_item = resp_data["added"][0]
        self.assertTrue(added_item["migrated"])
        self.assertEqual(added_item["rules_count"], 1)
        self.assertTrue(added_item["rules_enabled"])

        async with self.sessions() as session:
            account = (
                await session.execute(
                    select(Account).where(Account.account_id == "act_777000")
                )
            ).scalar_one()
            self.assertEqual(account.meta_connection_id, self.connection_id)
            self.assertEqual(account.access_token, "")
            self.assertEqual(account.access_token_encrypted, "")
            self.assertTrue(account.rules_enabled)
            self.assertEqual(
                json.loads(account.active_rules),
                [{"preset_id": 10, "name": "Auto Pause 10"}],
            )
            self.assertEqual(account.custom_name, "Custom 777")
            self.assertEqual(account.note, "Crucial note")
            self.assertTrue(account.is_active)

            audit = (
                await session.execute(
                    select(AuditEvent).where(
                        AuditEvent.event_type == "ACCOUNT_MIGRATED_TO_OAUTH"
                    )
                )
            ).scalar_one()
            self.assertEqual(audit.account_id, "act_777000")
            self.assertEqual(audit.before_state["connection_type"], "system_user")
            self.assertEqual(audit.before_state["rules_count"], 1)
            self.assertEqual(audit.after_state["connection_type"], "facebook_login")
            self.assertEqual(audit.after_state["meta_connection_id"], self.connection_id)

    async def test_migrate_enforces_workspace_isolation_and_rbac(self):
        async with self.sessions() as session:
            other_ws = Workspace(
                name="Other WS",
                slug="other-ws",
                owner_user_id=self.user_id,
            )
            session.add(other_ws)
            await session.flush()
            foreign_acc = Account(
                account_id="act_foreign_888",
                name="Foreign Acc",
                workspace_id=other_ws.id,
                owner_user_id=self.user_id,
                access_token_encrypted=encrypt_meta_token("EAAB-foreign"),
                meta_connection_id=None,
            )
            session.add(foreign_acc)
            await session.commit()

        fake_oauth = AsyncMock()
        fake_oauth.debug_token.return_value = {
            "is_valid": True,
            "app_id": settings.META_APP_ID,
            "scopes": ["ads_read", "ads_management", "business_management"],
        }
        fake_oauth.discover_ad_accounts.return_value = [
            {
                "id": "act_foreign_888",
                "name": "Foreign Acc",
                "account_status": 1,
                "currency": "USD",
                "timezone_name": "UTC",
                "business": {"id": "bm-foreign", "name": "Foreign BM"},
            }
        ]
        account_info = {
            "id": "act_foreign_888",
            "name": "Foreign Acc",
            "account_status": 1,
            "currency": "USD",
            "timezone_name": "UTC",
            "status_label": "Активен",
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
                await client.post(
                    f"/api/meta/connections/{self.connection_id}/discover",
                    headers=self.headers,
                )
                import_resp = await client.post(
                    f"/api/meta/connections/{self.connection_id}/import",
                    headers=self.headers,
                    json={"account_ids": ["act_foreign_888"]},
                )

        self.assertEqual(import_resp.status_code, 200)
        self.assertEqual(import_resp.json()["error_count"], 1)
        self.assertIn("другом рабочем пространстве", import_resp.json()["errors"][0]["error"])


if __name__ == "__main__":
    unittest.main()


