"""Tests for Meta invite-link flow (one-time secure Facebook profile connection).

Tests cover:
- POST /api/meta/invites — create invite (owner/buyer), viewer blocked
- GET /api/meta/invites — list
- DELETE /api/meta/invites/{id} — revoke
- GET /api/meta/invites/public/{token} — public validate
- GET /api/meta/oauth/invite/{token} — start OAuth via invite
- Replay attack prevention (used invite cannot start OAuth)
- Expired invite rejected
- Wrong-workspace isolation
"""

import hashlib
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import api.auth as auth_module
import api.meta_oauth as meta_oauth_module
import api.server as server_module
from api.server import create_app
from core.config import settings
from database.models import (
    MetaConnection,
    MetaConnectionInvite,
    MetaOAuthState,
    User,
    Workspace,
    WorkspaceMember,
)

from tests.test_db_helper import create_test_engine, init_test_db


def _now():
    return datetime.now(timezone.utc)


class TestMetaInvites(unittest.IsolatedAsyncioTestCase):
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

        self.orig = {
            k: getattr(settings, k)
            for k in ("META_APP_ID", "META_APP_SECRET", "META_LOGIN_CONFIG_ID",
                      "META_OAUTH_REDIRECT_URI", "META_TOKEN_ENCRYPTION_KEY", "WEBAPP_URL")
        }
        settings.META_APP_ID = "906676569173031"
        settings.META_APP_SECRET = "test-secret"
        settings.META_LOGIN_CONFIG_ID = "cfg-123"
        settings.META_OAUTH_REDIRECT_URI = "https://buyerly.app/api/meta/oauth/callback"
        settings.META_TOKEN_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")
        settings.WEBAPP_URL = "https://buyerly.app"

        async with self.sessions() as session:
            # Owner user
            owner = User(
                telegram_id="90001",
                username="invite-owner",
                full_name="Invite Owner",
                auth_token="token-owner",
                role="buyer",
                is_approved=True,
            )
            session.add(owner)
            await session.flush()
            ws = Workspace(name="Invite WS", slug="invite-ws", owner_user_id=owner.id)
            session.add(ws)
            await session.flush()
            session.add(WorkspaceMember(workspace_id=ws.id, user_id=owner.id, role="owner"))
            owner.active_workspace_id = ws.id

            # Viewer user
            viewer = User(
                telegram_id="90002",
                username="invite-viewer",
                full_name="Viewer",
                auth_token="token-viewer",
                role="buyer",
                is_approved=True,
            )
            session.add(viewer)
            await session.flush()
            session.add(WorkspaceMember(workspace_id=ws.id, user_id=viewer.id, role="viewer"))
            viewer.active_workspace_id = ws.id

            # Buyer user
            buyer = User(
                telegram_id="90003",
                username="invite-buyer",
                full_name="Buyer",
                auth_token="token-buyer",
                role="buyer",
                is_approved=True,
            )
            session.add(buyer)
            await session.flush()
            session.add(WorkspaceMember(workspace_id=ws.id, user_id=buyer.id, role="buyer"))
            buyer.active_workspace_id = ws.id

            await session.commit()
            await session.refresh(owner)
            await session.refresh(ws)
            await session.refresh(viewer)
            await session.refresh(buyer)

            self.owner_id = owner.id
            self.viewer_id = viewer.id
            self.buyer_id = buyer.id
            self.ws_id = ws.id

        self.app = create_app()

    async def asyncTearDown(self):
        for k, v in self.orig.items():
            setattr(settings, k, v)
        await self.engine.dispose()

    def _auth(self, token: str):
        return {"Authorization": f"Bearer {token}"}

    def _client(self):
        transport = httpx.ASGITransport(app=self.app)
        return httpx.AsyncClient(transport=transport, base_url="http://test")

    # ------------------------------------------------------------------
    # Create invite
    # ------------------------------------------------------------------

    async def test_create_invite_owner_returns_url(self):
        async with self._client() as client:
            resp = await client.post(
                "/api/meta/invites",
                json={"label": "Buyer Ivan", "expires_in_hours": 6},
                headers=self._auth("token-owner"),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertIn("invite_url", data)
        self.assertIn("raw_token", data)
        self.assertTrue(data["invite_url"].startswith("https://buyerly.app/connect/meta/"))
        self.assertEqual(data["label"], "Buyer Ivan")
        self.assertEqual(data["status"], "pending")
        self.assertTrue(data["raw_token"].startswith("inv_fb_"))

    async def test_create_invite_buyer_role_allowed(self):
        async with self._client() as client:
            resp = await client.post(
                "/api/meta/invites",
                json={"label": "", "expires_in_hours": 24},
                headers=self._auth("token-buyer"),
            )
        self.assertEqual(resp.status_code, 200)

    async def test_create_invite_viewer_blocked(self):
        async with self._client() as client:
            resp = await client.post(
                "/api/meta/invites",
                json={"label": "X", "expires_in_hours": 24},
                headers=self._auth("token-viewer"),
            )
        self.assertEqual(resp.status_code, 403)

    async def test_create_invite_unauthenticated_blocked(self):
        async with self._client() as client:
            resp = await client.post("/api/meta/invites", json={"label": "", "expires_in_hours": 24})
        self.assertIn(resp.status_code, (401, 403))

    async def test_create_invite_token_hash_not_in_response(self):
        """raw_token is exposed on creation, but token_hash must never be exposed."""
        async with self._client() as client:
            resp = await client.post(
                "/api/meta/invites",
                json={"label": "Test", "expires_in_hours": 1},
                headers=self._auth("token-owner"),
            )
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("token_hash", resp.json())

    # ------------------------------------------------------------------
    # List invites
    # ------------------------------------------------------------------

    async def test_list_invites_empty(self):
        async with self._client() as client:
            resp = await client.get("/api/meta/invites", headers=self._auth("token-owner"))
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json(), list)

    async def test_list_invites_shows_created_invite(self):
        async with self._client() as client:
            await client.post(
                "/api/meta/invites",
                json={"label": "Listed", "expires_in_hours": 24},
                headers=self._auth("token-owner"),
            )
            resp = await client.get("/api/meta/invites", headers=self._auth("token-owner"))
        self.assertEqual(resp.status_code, 200)
        invites = resp.json()
        self.assertGreater(len(invites), 0)
        labels = [i["label"] for i in invites]
        self.assertIn("Listed", labels)

    async def test_list_invites_does_not_expose_raw_url(self):
        """After creation, list endpoint must NOT re-expose the full invite URL."""
        async with self._client() as client:
            await client.post(
                "/api/meta/invites",
                json={"label": "Secret", "expires_in_hours": 24},
                headers=self._auth("token-owner"),
            )
            resp = await client.get("/api/meta/invites", headers=self._auth("token-owner"))
        invites = resp.json()
        for inv in invites:
            self.assertIsNone(inv.get("invite_url"))

    # ------------------------------------------------------------------
    # Revoke invite
    # ------------------------------------------------------------------

    async def test_revoke_pending_invite(self):
        async with self._client() as client:
            create_resp = await client.post(
                "/api/meta/invites",
                json={"label": "To revoke", "expires_in_hours": 24},
                headers=self._auth("token-owner"),
            )
            invite_id = create_resp.json()["id"]
            resp = await client.delete(
                f"/api/meta/invites/{invite_id}", headers=self._auth("token-owner")
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "revoked")

    async def test_revoke_nonexistent_returns_404(self):
        async with self._client() as client:
            resp = await client.delete("/api/meta/invites/999999", headers=self._auth("token-owner"))
        self.assertEqual(resp.status_code, 404)

    async def test_revoke_already_used_returns_409(self):
        """Cannot revoke an already-used invite."""
        now = _now()
        async with self.sessions() as session:
            raw_token = "inv_fb_used_test_token"
            token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
            invite = MetaConnectionInvite(
                workspace_id=self.ws_id,
                created_by_user_id=self.owner_id,
                token_hash=token_hash,
                token_prefix="inv_fb_used_te...",
                label="Already used",
                status="used",
                used_at=now,
                expires_at=now + timedelta(hours=24),
            )
            session.add(invite)
            await session.commit()
            await session.refresh(invite)
            invite_id = invite.id

        async with self._client() as client:
            resp = await client.delete(
                f"/api/meta/invites/{invite_id}", headers=self._auth("token-owner")
            )
        self.assertEqual(resp.status_code, 409)

    # ------------------------------------------------------------------
    # Public invite info
    # ------------------------------------------------------------------

    async def test_public_invite_info_valid(self):
        async with self._client() as client:
            create_resp = await client.post(
                "/api/meta/invites",
                json={"label": "Public check", "expires_in_hours": 24},
                headers=self._auth("token-owner"),
            )
            raw_token = create_resp.json()["raw_token"]
            resp = await client.get(f"/api/meta/invites/public/{raw_token}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["valid"])
        self.assertEqual(data["status"], "pending")
        self.assertEqual(data["workspace_name"], "Invite WS")
        self.assertEqual(data["label"], "Public check")

    async def test_public_invite_info_invalid_token(self):
        async with self._client() as client:
            resp = await client.get("/api/meta/invites/public/inv_fb_INVALID_TOKEN_XYZ")
        self.assertEqual(resp.status_code, 404)

    async def test_public_invite_info_expired(self):
        now = _now()
        async with self.sessions() as session:
            raw_token = "inv_fb_expired_test_abc123"
            token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
            invite = MetaConnectionInvite(
                workspace_id=self.ws_id,
                created_by_user_id=self.owner_id,
                token_hash=token_hash,
                token_prefix="inv_fb_expired...",
                label="Expired",
                status="pending",
                expires_at=now - timedelta(hours=1),
            )
            session.add(invite)
            await session.commit()

        async with self._client() as client:
            resp = await client.get(f"/api/meta/invites/public/{raw_token}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["valid"])
        self.assertEqual(data["status"], "expired")

    async def test_public_invite_info_revoked(self):
        now = _now()
        async with self.sessions() as session:
            raw_token = "inv_fb_revoked_test_abc123"
            token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
            invite = MetaConnectionInvite(
                workspace_id=self.ws_id,
                created_by_user_id=self.owner_id,
                token_hash=token_hash,
                token_prefix="inv_fb_revoked...",
                label="Revoked",
                status="revoked",
                expires_at=now + timedelta(hours=24),
            )
            session.add(invite)
            await session.commit()

        async with self._client() as client:
            resp = await client.get(f"/api/meta/invites/public/{raw_token}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["valid"])
        self.assertEqual(data["status"], "revoked")

    # ------------------------------------------------------------------
    # OAuth via invite
    # ------------------------------------------------------------------

    async def test_oauth_invite_valid_redirects_to_facebook(self):
        async with self._client() as client:
            create_resp = await client.post(
                "/api/meta/invites",
                json={"label": "OAuth test", "expires_in_hours": 1},
                headers=self._auth("token-owner"),
            )
            raw_token = create_resp.json()["raw_token"]

            mock_client = MagicMock()
            mock_client.build_authorization_url.return_value = "https://www.facebook.com/dialog/oauth?state=xyz"
            with patch("api.meta_oauth._oauth_client", return_value=mock_client):
                resp = await client.get(
                    f"/api/meta/oauth/invite/{raw_token}", follow_redirects=False
                )
        self.assertEqual(resp.status_code, 303)
        self.assertIn("facebook.com", resp.headers.get("location", ""))

    async def test_oauth_invite_invalid_token_redirects_to_error(self):
        async with self._client() as client:
            resp = await client.get(
                "/api/meta/oauth/invite/inv_fb_COMPLETELY_INVALID_TOKEN_99",
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 303)
        self.assertIn("invite_invalid", resp.headers.get("location", ""))

    async def test_oauth_invite_expired_redirects_to_error(self):
        now = _now()
        async with self.sessions() as session:
            raw_token = "inv_fb_expired_oauth_test"
            token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
            session.add(MetaConnectionInvite(
                workspace_id=self.ws_id,
                created_by_user_id=self.owner_id,
                token_hash=token_hash,
                token_prefix="inv_fb_expired...",
                label="Expired OAuth",
                status="pending",
                expires_at=now - timedelta(seconds=1),
            ))
            await session.commit()

        async with self._client() as client:
            resp = await client.get(
                f"/api/meta/oauth/invite/{raw_token}", follow_redirects=False
            )
        self.assertEqual(resp.status_code, 303)
        self.assertIn("invite_invalid", resp.headers.get("location", ""))

    async def test_oauth_invite_revoked_redirects_to_error(self):
        now = _now()
        async with self.sessions() as session:
            raw_token = "inv_fb_revoked_oauth_test_abc"
            token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
            session.add(MetaConnectionInvite(
                workspace_id=self.ws_id,
                created_by_user_id=self.owner_id,
                token_hash=token_hash,
                token_prefix="inv_fb_revoked...",
                label="Revoked OAuth",
                status="revoked",
                expires_at=now + timedelta(hours=24),
            ))
            await session.commit()

        async with self._client() as client:
            resp = await client.get(
                f"/api/meta/oauth/invite/{raw_token}", follow_redirects=False
            )
        self.assertEqual(resp.status_code, 303)
        self.assertIn("invite_invalid", resp.headers.get("location", ""))

    async def test_oauth_invite_creates_oauth_state_with_invite_id(self):
        """Starting OAuth via invite must create an OAuthState record with invite_id set."""
        async with self._client() as client:
            create_resp = await client.post(
                "/api/meta/invites",
                json={"label": "State check", "expires_in_hours": 24},
                headers=self._auth("token-owner"),
            )
            raw_token = create_resp.json()["raw_token"]
            invite_id = create_resp.json()["id"]

            mock_client = MagicMock()
            mock_client.build_authorization_url.return_value = "https://www.facebook.com/dialog/oauth?state=xyz"
            with patch("api.meta_oauth._oauth_client", return_value=mock_client):
                await client.get(
                    f"/api/meta/oauth/invite/{raw_token}", follow_redirects=False
                )

        async with self.sessions() as session:
            state = (await session.execute(
                select(MetaOAuthState).where(MetaOAuthState.invite_id == invite_id)
            )).scalar_one_or_none()
        self.assertIsNotNone(state)
        self.assertEqual(state.invite_id, invite_id)
        self.assertEqual(state.workspace_id, self.ws_id)

    async def test_revoked_invite_cannot_complete_an_oauth_flow_already_in_progress(self):
        async with self._client() as client:
            create_resp = await client.post(
                "/api/meta/invites",
                json={"label": "Revoke during OAuth", "expires_in_hours": 24},
                headers=self._auth("token-owner"),
            )
            raw_token = create_resp.json()["raw_token"]
            invite_id = create_resp.json()["id"]

            start_client = MagicMock()
            start_client.build_authorization_url.side_effect = (
                lambda state: f"https://www.facebook.com/dialog/oauth?state={state}"
            )
            with patch("api.meta_oauth._oauth_client", return_value=start_client):
                start_resp = await client.get(
                    f"/api/meta/oauth/invite/{raw_token}", follow_redirects=False
                )

            from urllib.parse import parse_qs, urlparse

            state = parse_qs(urlparse(start_resp.headers["location"]).query)["state"][0]
            revoke_resp = await client.delete(
                f"/api/meta/invites/{invite_id}", headers=self._auth("token-owner")
            )
            self.assertEqual(revoke_resp.status_code, 200)

            callback_client = AsyncMock()
            callback_client.exchange_code.return_value = {
                "access_token": "EAAB-revoked-invite-token",
                "identity": {"id": "meta-user-revoked-invite", "name": "Revoked Invite User"},
                "debug": {
                    "is_valid": True,
                    "app_id": settings.META_APP_ID,
                    "scopes": ["ads_read", "ads_management", "business_management"],
                    "expires_at": 1893456000,
                },
            }
            with patch("api.meta_oauth._oauth_client", return_value=callback_client):
                callback_resp = await client.get(
                    f"/api/meta/oauth/callback?state={state}&code=revoked-invite-code",
                    follow_redirects=False,
                )

        self.assertEqual(callback_resp.status_code, 303)
        self.assertIn("meta_status=invite_invalid", callback_resp.headers["location"])
        async with self.sessions() as session:
            connection = (
                await session.execute(
                    select(MetaConnection).where(
                        MetaConnection.provider_user_id == "meta-user-revoked-invite"
                    )
                )
            ).scalar_one_or_none()
            invite = await session.get(MetaConnectionInvite, invite_id)
        self.assertIsNone(connection)
        self.assertEqual(invite.status, "revoked")
        self.assertIsNone(invite.connected_meta_id)

    async def test_replay_attack_used_invite_cannot_start_oauth(self):
        """A used invite cannot be used to start another OAuth flow."""
        now = _now()
        async with self.sessions() as session:
            raw_token = "inv_fb_used_replay_attack_test"
            token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
            session.add(MetaConnectionInvite(
                workspace_id=self.ws_id,
                created_by_user_id=self.owner_id,
                token_hash=token_hash,
                token_prefix="inv_fb_used_re...",
                label="Replay",
                status="used",
                used_at=now - timedelta(hours=1),
                expires_at=now + timedelta(hours=24),
            ))
            await session.commit()

        async with self._client() as client:
            resp = await client.get(
                f"/api/meta/oauth/invite/{raw_token}", follow_redirects=False
            )
        self.assertEqual(resp.status_code, 303)
        self.assertIn("invite_invalid", resp.headers.get("location", ""))

    # ------------------------------------------------------------------
    # Workspace isolation
    # ------------------------------------------------------------------

    async def test_invite_workspace_isolation(self):
        """Invites from workspace A must not be revocable via workspace B member."""
        async with self.sessions() as session:
            other_user = User(
                telegram_id="90099",
                username="other-user-inv",
                full_name="Other",
                auth_token="token-other-inv",
                role="buyer",
                is_approved=True,
            )
            session.add(other_user)
            await session.flush()
            other_ws = Workspace(name="Other WS Inv", slug="other-ws-inv", owner_user_id=other_user.id)
            session.add(other_ws)
            await session.flush()
            session.add(WorkspaceMember(workspace_id=other_ws.id, user_id=other_user.id, role="owner"))
            other_user.active_workspace_id = other_ws.id

            raw_token = "inv_fb_other_ws_isolation_test"
            token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
            other_invite = MetaConnectionInvite(
                workspace_id=other_ws.id,
                created_by_user_id=other_user.id,
                token_hash=token_hash,
                token_prefix="inv_fb_other...",
                label="Other WS",
                status="pending",
                expires_at=_now() + timedelta(hours=24),
            )
            session.add(other_invite)
            await session.commit()
            await session.refresh(other_invite)
            other_invite_id = other_invite.id

        async with self._client() as client:
            resp = await client.delete(
                f"/api/meta/invites/{other_invite_id}", headers=self._auth("token-owner")
            )
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
