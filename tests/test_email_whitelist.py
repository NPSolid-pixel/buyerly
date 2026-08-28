import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import api.auth as api_auth_module
import api.routes as api_routes_module
import api.routers.auth as auth_router_module
import api.server as api_server_module
import bot.handlers as bot_handlers
import database.db as database_db_module
from api.server import create_app
from core.config import settings
from core.rate_limit import limiter
from database.db import hash_password
from database.models import (
    AllowedEmail,
    User,
    WebSession,
    Workspace,
    WorkspaceInvite,
    WorkspaceMember,
)
from tests.test_db_helper import create_test_engine, init_test_db


class TestEmailWhitelistAccess(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.limiter_patcher = patch.object(limiter, "is_allowed", new_callable=AsyncMock)
        self.mock_limiter = self.limiter_patcher.start()
        self.mock_limiter.return_value = (True, 0)

        self.original_auth_session_maker = auth_router_module.async_session_maker
        self.original_bot_session_maker = bot_handlers.async_session_maker
        self.original_api_session_maker = api_auth_module.async_session_maker
        self.original_routes_session_maker = api_routes_module.async_session_maker
        self.original_server_session_maker = api_server_module.async_session_maker
        self.original_db_session_maker = database_db_module.async_session_maker
        self.original_admin_chat_id = settings.ADMIN_CHAT_ID
        settings.ADMIN_CHAT_ID = "123456789"

        self.engine = create_test_engine()
        self.sessions = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        await init_test_db(self.engine)

        auth_router_module.async_session_maker = self.sessions
        bot_handlers.async_session_maker = self.sessions
        api_auth_module.async_session_maker = self.sessions
        api_routes_module.async_session_maker = self.sessions
        api_server_module.async_session_maker = self.sessions
        database_db_module.async_session_maker = self.sessions

        self.app = create_app()

        # Seed admin and buyer
        async with self.sessions() as session:
            admin = User(
                telegram_id="123456789",
                username="admin_user",
                full_name="Admin User",
                email="admin@buyerly.com",
                password_hash=hash_password("adminpassword123"),
                role="admin",
                is_approved=True,
            )
            buyer = User(
                telegram_id="987654321",
                username="buyer_user",
                full_name="Buyer User",
                email="buyer@buyerly.com",
                password_hash=hash_password("buyerpassword123"),
                role="buyer",
                is_approved=True,
            )
            session.add_all([admin, buyer])
            await session.commit()
            await session.refresh(admin)
            await session.refresh(buyer)
            self.admin_id = admin.id
            self.buyer_id = buyer.id

    async def asyncTearDown(self):
        self.limiter_patcher.stop()
        auth_router_module.async_session_maker = self.original_auth_session_maker
        bot_handlers.async_session_maker = self.original_bot_session_maker
        api_auth_module.async_session_maker = self.original_api_session_maker
        api_routes_module.async_session_maker = self.original_routes_session_maker
        api_server_module.async_session_maker = self.original_server_session_maker
        database_db_module.async_session_maker = self.original_db_session_maker
        settings.ADMIN_CHAT_ID = self.original_admin_chat_id
        await self.engine.dispose()

    async def test_unlisted_email_rejected_on_request_temporary_password(self):
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
            resp = await client.post(
                "/api/auth/request-temporary-password",
                json={"email": "stranger@random.com"},
            )
            self.assertEqual(resp.status_code, 403)
            self.assertIn("не найден в списке разрешенных", resp.json()["detail"])

    async def test_whitelisted_email_allowed_on_request_temporary_password(self):
        async with self.sessions() as session:
            session.add(AllowedEmail(email="allowed.buyer@agency.com", added_by="admin"))
            await session.commit()

        with patch("api.routers.auth.send_otp_verification_email", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
                resp = await client.post(
                    "/api/auth/request-temporary-password",
                    json={"email": "ALLOWED.BUYER@agency.com"},
                )
                self.assertEqual(resp.status_code, 200)
                self.assertTrue(resp.json()["ok"])
                self.assertEqual(mock_send.call_count, 1)

    async def test_invited_email_allowed_on_request_temporary_password(self):
        from datetime import datetime, timedelta, timezone
        async with self.sessions() as session:
            ws = Workspace(name="Test WS", slug="test-ws", owner_user_id=self.admin_id)
            session.add(ws)
            await session.flush()
            invite = WorkspaceInvite(
                workspace_id=ws.id,
                inviter_user_id=self.admin_id,
                email="invited.partner@agency.com",
                role="buyer",
                token="invite-token-123",
                used_count=0,
                max_uses=1,
                expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            )
            session.add(invite)
            await session.commit()

        with patch("api.routers.auth.send_otp_verification_email", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
                resp = await client.post(
                    "/api/auth/request-temporary-password",
                    json={"email": "invited.partner@agency.com"},
                )
                self.assertEqual(resp.status_code, 200)
                self.assertTrue(resp.json()["ok"])

    async def test_admin_whitelist_api_crud(self):
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
            # Login as admin
            login_resp = await client.post(
                "/api/auth/login",
                json={"username": "admin_user", "password": "adminpassword123"},
            )
            self.assertEqual(login_resp.status_code, 200)
            csrf_token = client.cookies.get("buyerly_csrf")

            # 1. Add email
            add_resp = await client.post(
                "/api/auth/admin/allowed-emails",
                headers={"X-CSRF-Token": csrf_token} if csrf_token else {},
                json={"email": "NewBuyer@traffic.com", "comment": "Lead Buyer"},
            )
            self.assertEqual(add_resp.status_code, 200)
            data = add_resp.json()
            self.assertEqual(data["email"], "newbuyer@traffic.com")
            self.assertEqual(data["comment"], "Lead Buyer")
            email_id = data["id"]

            # 2. List emails
            list_resp = await client.get("/api/auth/admin/allowed-emails")
            self.assertEqual(list_resp.status_code, 200)
            emails = list_resp.json()
            self.assertTrue(any(e["id"] == email_id for e in emails))

            # 3. Delete email
            del_resp = await client.delete(
                f"/api/auth/admin/allowed-emails/{email_id}",
                headers={"X-CSRF-Token": csrf_token} if csrf_token else {},
            )
            self.assertEqual(del_resp.status_code, 200)

            # 4. Verify deleted
            list_resp2 = await client.get("/api/auth/admin/allowed-emails")
            emails2 = list_resp2.json()
            self.assertFalse(any(e["id"] == email_id for e in emails2))

    async def test_buyer_forbidden_from_admin_whitelist_api(self):
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
            # Login as buyer
            login_resp = await client.post(
                "/api/auth/login",
                json={"username": "buyer_user", "password": "buyerpassword123"},
            )
            self.assertEqual(login_resp.status_code, 200)
            csrf_token = client.cookies.get("buyerly_csrf")

            # Attempt to call admin endpoints
            get_resp = await client.get("/api/auth/admin/allowed-emails")
            self.assertEqual(get_resp.status_code, 403)

            add_resp = await client.post(
                "/api/auth/admin/allowed-emails",
                headers={"X-CSRF-Token": csrf_token} if csrf_token else {},
                json={"email": "hacker@test.com"},
            )
            self.assertEqual(add_resp.status_code, 403)

    async def test_revocation_cascades_to_user_approval_and_sessions(self):
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
            # Admin adds email for new user
            async with self.sessions() as session:
                entry = AllowedEmail(email="revokeme@team.com", added_by="admin")
                session.add(entry)
                await session.commit()
                await session.refresh(entry)
                entry_id = entry.id

            # User logs in and gets session
            async with self.sessions() as session:
                user = User(
                    username="revokeme",
                    email="revokeme@team.com",
                    password_hash=hash_password("pass123456"),
                    role="buyer",
                    is_approved=True,
                )
                session.add(user)
                await session.commit()
                await session.refresh(user)
                target_user_id = user.id

                from datetime import datetime, timedelta, timezone
                session.add(WebSession(
                    id="session-revokeme-123",
                    user_id=target_user_id,
                    token_hash="hash-token-revokeme-123",
                    csrf_hash="csrf-hash-revokeme-123",
                    expires_at=datetime.now(timezone.utc) + timedelta(days=1),
                ))
                await session.commit()

            # Admin deletes email via API
            admin_login = await client.post(
                "/api/auth/login",
                json={"username": "admin_user", "password": "adminpassword123"},
            )
            self.assertEqual(admin_login.status_code, 200)
            csrf_token = client.cookies.get("buyerly_csrf")

            del_resp = await client.delete(
                f"/api/auth/admin/allowed-emails/{entry_id}",
                headers={"X-CSRF-Token": csrf_token} if csrf_token else {},
            )
            self.assertEqual(del_resp.status_code, 200)

            # Check that user is no longer approved and web session was dropped
            async with self.sessions() as session:
                u = (await session.execute(select(User).where(User.id == target_user_id))).scalar_one()
                self.assertFalse(u.is_approved)

                active_sessions = (await session.execute(
                    select(WebSession).where(WebSession.user_id == target_user_id)
                )).scalars().all()
                self.assertEqual(len(active_sessions), 0)

    async def test_bot_allow_and_revoke_commands(self):
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=123456789, username="admin_user", full_name="Admin"),
            text="/allow_email telegram.buyer@agency.com Media Buyer Alex",
            answer=AsyncMock(),
        )
        bot = AsyncMock()
        state = AsyncMock()

        # Add email via command
        await bot_handlers.cmd_allow_email(message, bot, state)
        message.answer.assert_called_once()
        self.assertIn("успешно добавлен", message.answer.call_args[0][0])

        async with self.sessions() as session:
            entry = (await session.execute(
                select(AllowedEmail).where(AllowedEmail.email == "telegram.buyer@agency.com")
            )).scalar_one_or_none()
            self.assertIsNotNone(entry)
            self.assertEqual(entry.comment, "Media Buyer Alex")

        # Revoke email via command
        message2 = SimpleNamespace(
            from_user=SimpleNamespace(id=123456789, username="admin_user", full_name="Admin"),
            text="/revoke_email telegram.buyer@agency.com",
            answer=AsyncMock(),
        )
        await bot_handlers.cmd_revoke_email(message2, bot, state)
        message2.answer.assert_called_once()
        self.assertIn("удален из белого списка", message2.answer.call_args[0][0])

        async with self.sessions() as session:
            entry2 = (await session.execute(
                select(AllowedEmail).where(AllowedEmail.email == "telegram.buyer@agency.com")
            )).scalar_one_or_none()
            self.assertIsNone(entry2)

    async def test_bot_add_email_fsm_message_handler(self):
        state = AsyncMock()
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=123456789, username="admin_user", full_name="Admin"),
            text="batch1@team.com, batch2@team.com batch3@team.com",
            answer=AsyncMock(),
        )

        await bot_handlers.process_admin_add_email(message, state)
        message.answer.assert_called_once()
        self.assertIn("Добавлены в белый список", message.answer.call_args[0][0])

        async with self.sessions() as session:
            emails = (await session.execute(select(AllowedEmail.email))).scalars().all()
            self.assertIn("batch1@team.com", emails)
            self.assertIn("batch2@team.com", emails)
            self.assertIn("batch3@team.com", emails)

    async def test_bot_delete_callback_safe_id(self):
        async with self.sessions() as session:
            entry = AllowedEmail(
                email="super.long.email.that.would.exceed.sixty.four.bytes.limit@domain.company.com",
                added_by="admin",
            )
            session.add(entry)
            await session.commit()
            await session.refresh(entry)
            entry_id = entry.id

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=123456789, username="admin_user", full_name="Admin"),
            data=f"del_em:{entry_id}",
            answer=AsyncMock(),
            message=SimpleNamespace(edit_text=AsyncMock()),
        )

        await bot_handlers.cb_delete_allowed_email(callback)
        callback.answer.assert_called_once()
        self.assertIn("удален из белого списка", callback.answer.call_args[0][0])

        async with self.sessions() as session:
            entry_after = (await session.execute(
                select(AllowedEmail).where(AllowedEmail.id == entry_id)
            )).scalar_one_or_none()
            self.assertIsNone(entry_after)
