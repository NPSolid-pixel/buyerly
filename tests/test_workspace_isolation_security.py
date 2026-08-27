import json
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import api.auth as api_auth_module
import api.routes as api_routes_module
import api.server as api_server_module
from api.server import create_app
from core.config import settings
from database.db import hash_password
from database.models import (
    Account,
    AuditEvent,
    RulePreset,
    User,
    Workspace,
    WorkspaceMember,
    WorkspaceSupportGrant,
)
from tests.test_api import generate_valid_telegram_init_data
from tests.test_db_helper import create_test_engine, init_test_db


class TestWorkspaceIsolationSecurity(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        api_routes_module._summary_cache.clear()
        self.test_engine = create_test_engine()
        self.test_session_maker = async_sessionmaker(
            self.test_engine, class_=AsyncSession, expire_on_commit=False
        )
        await init_test_db(self.test_engine)

        api_routes_module.async_session_maker = self.test_session_maker
        api_auth_module.async_session_maker = self.test_session_maker
        api_server_module.async_session_maker = self.test_session_maker

        settings.BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
        settings.ADMIN_CHAT_ID = "8634201356"

        async with self.test_session_maker() as session:
            # 1. Platform Admin user
            self.admin_user = User(
                telegram_id="999000111",
                username="admin_user",
                full_name="Platform Admin",
                password_hash=hash_password("admin-password"),
                role="admin",
                is_approved=True,
            )
            session.add(self.admin_user)

            # 2. Tenant A owner
            self.tenant_a_user = User(
                telegram_id="111000111",
                username="tenant_a",
                full_name="Tenant A Owner",
                password_hash=hash_password("tenant-a-password"),
                role="buyer",
                is_approved=True,
            )
            session.add(self.tenant_a_user)

            # 3. Tenant B owner
            self.tenant_b_user = User(
                telegram_id="222000222",
                username="tenant_b",
                full_name="Tenant B Owner",
                password_hash=hash_password("tenant-b-password"),
                role="buyer",
                is_approved=True,
            )
            session.add(self.tenant_b_user)
            await session.flush()

            # Workspace for Admin
            self.ws_admin = Workspace(
                name="Admin HQ",
                slug="admin-hq",
                badge_text="A",
                badge_color="#3B82F6",
                owner_user_id=self.admin_user.id,
            )
            session.add(self.ws_admin)

            # Workspace A
            self.ws_a = Workspace(
                name="Tenant A Space",
                slug="tenant-a-space",
                badge_text="TA",
                badge_color="#10B981",
                owner_user_id=self.tenant_a_user.id,
            )
            session.add(self.ws_a)

            # Workspace B
            self.ws_b = Workspace(
                name="Tenant B Space",
                slug="tenant-b-space",
                badge_text="TB",
                badge_color="#F59E0B",
                owner_user_id=self.tenant_b_user.id,
            )
            session.add(self.ws_b)
            await session.flush()

            # Memberships
            session.add(WorkspaceMember(workspace_id=self.ws_admin.id, user_id=self.admin_user.id, role="owner"))
            session.add(WorkspaceMember(workspace_id=self.ws_a.id, user_id=self.tenant_a_user.id, role="owner"))
            session.add(WorkspaceMember(workspace_id=self.ws_b.id, user_id=self.tenant_b_user.id, role="owner"))

            self.admin_user.active_workspace_id = self.ws_admin.id
            self.tenant_a_user.active_workspace_id = self.ws_a.id
            self.tenant_b_user.active_workspace_id = self.ws_b.id

            # Accounts
            self.acc_a = Account(
                account_id="act_111111",
                name="Tenant A Account",
                workspace_id=self.ws_a.id,
                owner_user_id=self.tenant_a_user.id,
                timezone_name="UTC",
                currency="USD",
                is_active=True,
            )
            self.acc_b = Account(
                account_id="act_222222",
                name="Tenant B Account",
                workspace_id=self.ws_b.id,
                owner_user_id=self.tenant_b_user.id,
                timezone_name="UTC",
                currency="USD",
                is_active=True,
            )
            session.add_all([self.acc_a, self.acc_b])

            # Audit events
            self.audit_b = AuditEvent(
                workspace_id=self.ws_b.id,
                owner_user_id=self.tenant_b_user.id,
                actor_type="user",
                actor_id=str(self.tenant_b_user.telegram_id),
                category="MANUAL_ACTION",
                event_type="UPDATE_ACCOUNT_PROFILE",
                status="SUCCESS",
                account_id="act_222222",
                account_name="Tenant B Account",
                message="Profile updated",
            )
            session.add(self.audit_b)

            await session.commit()

        self.app = create_app()

    async def asyncTearDown(self):
        await self.test_engine.dispose()

    def _headers_for(self, user: User) -> dict:
        tma_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            {"id": int(user.telegram_id), "first_name": user.full_name, "username": user.username},
        )
        return {"Authorization": f"tma {tma_data}"}

    async def test_global_admin_cannot_leak_or_mutate_foreign_accounts(self):
        """Verify global admin cannot see or mutate foreign workspace accounts without membership."""
        admin_headers = self._headers_for(self.admin_user)
        transport = httpx.ASGITransport(app=self.app)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Admin gets accounts -> must only see Admin HQ accounts (0 accounts), NOT Tenant B
            res = await client.get("/api/accounts", headers=admin_headers)
            self.assertEqual(res.status_code, 200)
            accounts = res.json()
            self.assertEqual(len(accounts), 0)

            # 2. Admin tries to mutate Tenant B account -> 404
            patch_res = await client.patch(
                "/api/accounts/act_222222/profile",
                headers=admin_headers,
                json={"custom_name": "Hacked Name", "note": "Hacked Note"},
            )
            self.assertEqual(patch_res.status_code, 404)

            # Verify security audit event was logged for blocked mutation
            async with self.test_session_maker() as session:
                security_event = (
                    await session.execute(
                        select(AuditEvent).where(
                            AuditEvent.category == "SECURITY",
                            AuditEvent.event_type == "UNAUTHORIZED_ACCESS_ATTEMPT",
                        )
                    )
                ).scalar_one_or_none()
                self.assertIsNotNone(security_event)
                self.assertEqual(security_event.owner_user_id, self.admin_user.id)

            # 3. Admin tries to delete Tenant B account -> 404
            del_res = await client.delete("/api/accounts/act_222222", headers=admin_headers)
            self.assertEqual(del_res.status_code, 404)

    async def test_global_admin_cannot_switch_workspace_without_grant(self):
        """Admin cannot switch to Tenant B workspace without explicit membership or support grant."""
        admin_headers = self._headers_for(self.admin_user)
        transport = httpx.ASGITransport(app=self.app)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post(
                "/api/workspaces/switch",
                headers=admin_headers,
                json={"workspace_id": self.ws_b.id},
            )
            self.assertEqual(res.status_code, 403)

    async def test_viewer_role_write_access_strictly_blocked(self):
        """Workspace viewer is strictly blocked from write access, regardless of platform admin role."""
        async with self.test_session_maker() as session:
            viewer_user = User(
                telegram_id="333000333",
                username="viewer_user",
                full_name="Viewer User",
                password_hash=hash_password("viewer-password"),
                role="admin",  # Even with global role=admin!
                is_approved=True,
                active_workspace_id=self.ws_a.id,
            )
            session.add(viewer_user)
            await session.flush()
            session.add(WorkspaceMember(workspace_id=self.ws_a.id, user_id=viewer_user.id, role="viewer"))
            viewer_visible_event = AuditEvent(
                workspace_id=self.ws_a.id,
                owner_user_id=self.tenant_a_user.id,
                category="RULE_ACTION",
                event_type="STOP",
                status="SUCCESS",
                account_id=self.acc_a.account_id,
                adset_id="viewer-undo-target",
                action="STOP",
                before_state={"status": "ACTIVE"},
                after_state={"status": "PAUSED"},
            )
            session.add(viewer_visible_event)
            await session.commit()
            viewer_visible_event_id = viewer_visible_event.id

        viewer_headers = self._headers_for(viewer_user)
        transport = httpx.ASGITransport(app=self.app)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Read works
            get_res = await client.get("/api/accounts", headers=viewer_headers)
            self.assertEqual(get_res.status_code, 200)

            # Write is blocked with 403
            patch_res = await client.patch(
                "/api/accounts/act_111111/profile",
                headers=viewer_headers,
                json={"custom_name": "Viewer Edit", "note": "Blocked"},
            )
            self.assertEqual(patch_res.status_code, 403)
            self.assertIn("Viewer", patch_res.json()["detail"])

            audit_res = await client.get("/api/audit-events", headers=viewer_headers)
            self.assertEqual(audit_res.status_code, 200)
            viewer_item = next(
                item for item in audit_res.json()["items"]
                if item["id"] == viewer_visible_event_id
            )
            self.assertFalse(viewer_item["can_undo"])
            self.assertIn("Viewer", viewer_item["undo_reason"])

            undo_res = await client.post(
                f"/api/audit-events/{viewer_visible_event_id}/undo",
                headers=viewer_headers,
            )
            self.assertEqual(undo_res.status_code, 403)

    async def test_admin_support_session_lifecycle(self):
        """Admin creates bounded support session, accesses workspace, and revokes session."""
        admin_headers = self._headers_for(self.admin_user)
        transport = httpx.ASGITransport(app=self.app)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Non-admin cannot create support session
            tenant_headers = self._headers_for(self.tenant_a_user)
            denied_res = await client.post(
                "/api/admin/support-sessions",
                headers=tenant_headers,
                json={
                    "workspace_id": self.ws_b.id,
                    "reason": "Unauthorized attempt to access tenant B",
                    "duration_minutes": 30,
                },
            )
            self.assertEqual(denied_res.status_code, 403)

            # 2. Validation: reason too short
            short_res = await client.post(
                "/api/admin/support-sessions",
                headers=admin_headers,
                json={"workspace_id": self.ws_b.id, "reason": "short", "duration_minutes": 30},
            )
            self.assertEqual(short_res.status_code, 422)

            # 3. Create valid support session
            grant_res = await client.post(
                "/api/admin/support-sessions",
                headers=admin_headers,
                json={
                    "workspace_id": self.ws_b.id,
                    "reason": "Investigating payment webhook sync issue for client",
                    "duration_minutes": 30,
                },
            )
            self.assertEqual(grant_res.status_code, 200)
            grant_data = grant_res.json()
            self.assertTrue(grant_data["is_active"])
            self.assertEqual(grant_data["workspace_id"], self.ws_b.id)
            grant_id = grant_data["id"]

            # 4. Now Admin can switch into Workspace B
            switch_res = await client.post(
                "/api/workspaces/switch",
                headers=admin_headers,
                json={"workspace_id": self.ws_b.id},
            )
            self.assertEqual(switch_res.status_code, 200)

            # 5. Admin can see Tenant B accounts while in active support session
            acc_res = await client.get("/api/accounts", headers=admin_headers)
            self.assertEqual(acc_res.status_code, 200)
            accounts = acc_res.json()
            self.assertEqual(len(accounts), 1)
            self.assertEqual(accounts[0]["account_id"], "act_222222")

            # 6. Revoke support session
            revoke_res = await client.post(
                f"/api/admin/support-sessions/{grant_id}/revoke",
                headers=admin_headers,
            )
            self.assertEqual(revoke_res.status_code, 200)

            # 7. Verify support session is no longer active
            list_res = await client.get(
                "/api/admin/support-sessions?active_only=true",
                headers=admin_headers,
            )
            self.assertEqual(list_res.status_code, 200)
            self.assertEqual(len(list_res.json()), 0)

            # 8. Switching back to Workspace B is now blocked
            switch_blocked = await client.post(
                "/api/workspaces/switch",
                headers=admin_headers,
                json={"workspace_id": self.ws_b.id},
            )
            self.assertEqual(switch_blocked.status_code, 403)

    async def test_audit_events_and_undo_strict_workspace_scoping(self):
        """Verify audit events are strictly isolated per workspace and cross-workspace undo is blocked."""
        admin_headers = self._headers_for(self.admin_user)
        transport = httpx.ASGITransport(app=self.app)

        async with self.test_session_maker() as session:
            admin_audit_account = Account(
                account_id="shared-audit-account",
                name="Admin audit account",
                workspace_id=self.ws_admin.id,
                owner_user_id=self.admin_user.id,
                access_token="mock-token",
                timezone_name="UTC",
                currency="USD",
                is_active=True,
            )
            source = AuditEvent(
                workspace_id=self.ws_admin.id,
                owner_user_id=self.admin_user.id,
                category="RULE_ACTION",
                event_type="STOP",
                status="SUCCESS",
                account_id="shared-audit-account",
                adset_id="shared-audit-adset",
                action="STOP",
                before_state={"status": "ACTIVE"},
                after_state={"status": "PAUSED"},
            )
            legacy = AuditEvent(
                workspace_id=None,
                owner_user_id=self.admin_user.id,
                category="RULE_ACTION",
                event_type="STOP",
                status="SUCCESS",
                account_id="legacy-null-workspace",
                adset_id="legacy-null-adset",
                action="STOP",
                before_state={"status": "ACTIVE"},
                after_state={"status": "PAUSED"},
            )
            session.add_all([admin_audit_account, source, legacy])
            await session.flush()
            foreign_newer = AuditEvent(
                workspace_id=self.ws_b.id,
                owner_user_id=self.tenant_b_user.id,
                category="RULE_ACTION",
                event_type="MANUAL_REACTIVATE",
                status="SUCCESS",
                account_id="shared-audit-account",
                adset_id="shared-audit-adset",
                action="REACTIVATE_ADSET",
                before_state={"status": "PAUSED"},
                after_state={"status": "ACTIVE"},
            )
            session.add(foreign_newer)
            await session.commit()
            source_id = source.id

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Admin in Admin HQ only sees their own workspace audit events (not Tenant B's)
            audit_res = await client.get("/api/audit-events", headers=admin_headers)
            self.assertEqual(audit_res.status_code, 200)
            items = audit_res.json()["items"]
            self.assertFalse(any(item["account_id"] == "act_222222" for item in items))
            self.assertFalse(any(item["account_id"] == "legacy-null-workspace" for item in items))
            source_item = next(item for item in items if item["id"] == source_id)
            self.assertTrue(source_item["can_undo"])

            # 2. Cross-workspace undo attempt is blocked
            undo_res = await client.post(
                f"/api/audit-events/{self.audit_b.id}/undo",
                headers=admin_headers,
            )
            self.assertEqual(undo_res.status_code, 403)

            with (
                patch.object(
                    api_routes_module.meta_client,
                    "get_adset_state",
                    new=AsyncMock(
                        return_value={
                            "status": "PAUSED",
                            "daily_budget": 50.0,
                        }
                    ),
                ),
                patch.object(
                    api_routes_module.meta_client,
                    "set_adset_status",
                    new=AsyncMock(return_value=True),
                ),
            ):
                own_undo = await client.post(
                    f"/api/audit-events/{source_id}/undo",
                    headers=admin_headers,
                )
            self.assertEqual(own_undo.status_code, 200)


if __name__ == "__main__":
    unittest.main()
