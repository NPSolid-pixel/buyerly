import asyncio
import time
import unittest

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import api.auth as api_auth_module
import api.routes as api_routes_module
import api.server as api_server_module
from api.server import create_app
from core.config import settings
from core.rate_limit import RateLimiter, limiter
from database.db import Base, hash_password
from database.models import TelegramUser, Workspace


class TestRateLimiterCore(unittest.IsolatedAsyncioTestCase):

    async def test_sliding_window_rate_limiting(self):
        custom_limiter = RateLimiter(cleanup_interval_seconds=60)

        # 3 requests allowed with limit=3 in window=1s
        for i in range(3):
            allowed, retry_after = await custom_limiter.is_allowed("user_test", limit=3, window_seconds=1)
            self.assertTrue(allowed)
            self.assertEqual(retry_after, 0)

        # 4th request within 1s should be rejected with retry_after >= 1
        allowed, retry_after = await custom_limiter.is_allowed("user_test", limit=3, window_seconds=1)
        self.assertFalse(allowed)
        self.assertGreaterEqual(retry_after, 1)

        # Key reset enables immediate access
        await custom_limiter.reset("user_test")
        allowed, retry_after = await custom_limiter.is_allowed("user_test", limit=3, window_seconds=1)
        self.assertTrue(allowed)

    async def test_stale_records_cleanup(self):
        custom_limiter = RateLimiter(cleanup_interval_seconds=0)
        # Populate with simulated past timestamp
        custom_limiter._records["old_key"] = [time.time() - 700]
        custom_limiter._records["recent_key"] = [time.time()]

        # Trigger cleanup
        custom_limiter._cleanup_stale(time.time())
        self.assertNotIn("old_key", custom_limiter._records)
        self.assertIn("recent_key", custom_limiter._records)


from tests.test_db_helper import create_test_engine, init_test_db


class TestApiRateLimitingAndDosProtection(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        await limiter.reset()
        self.test_engine = create_test_engine()
        self.test_session_maker = async_sessionmaker(self.test_engine, class_=AsyncSession, expire_on_commit=False)
        await init_test_db(self.test_engine)

        api_routes_module.async_session_maker = self.test_session_maker
        api_auth_module.async_session_maker = self.test_session_maker
        api_server_module.async_session_maker = self.test_session_maker

        settings.BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
        settings.ADMIN_CHAT_ID = "8634201356"

        async with self.test_session_maker() as session:
            user = TelegramUser(
                telegram_id="8634201356",
                username="test_buyer",
                full_name="Test Buyer",
                email="buyer@test.com",
                password_hash=hash_password("correct-password"),
                role="buyer",
                is_approved=True,
                auth_token="test-valid-auth-token-12345",
            )
            session.add(user)
            ws = Workspace(
                name="Test Workspace",
                slug="test-workspace",
                owner_user_id=1,
            )
            session.add(ws)
            await session.commit()

        self.app = create_app()

    async def asyncTearDown(self):
        await limiter.reset()
        await self.test_engine.dispose()

    async def test_login_rate_limiting(self):
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Send 10 failed login requests (limit is 10/min)
            for _ in range(10):
                res = await client.post(
                    "/api/auth/login",
                    json={"username": "test_buyer", "password": "wrong-password"},
                )
                self.assertEqual(res.status_code, 401)

            # 11th request must be rate-limited (HTTP 429)
            rate_limited_res = await client.post(
                "/api/auth/login",
                json={"username": "test_buyer", "password": "correct-password"},
            )
            self.assertEqual(rate_limited_res.status_code, 429)
            self.assertIn("Retry-After", rate_limited_res.headers)
            self.assertIn("Слишком много запросов", rate_limited_res.json()["detail"])

    async def test_check_slug_rate_limiting(self):
        headers = {"Authorization": "Bearer test-valid-auth-token-12345"}
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 30 requests allowed
            for i in range(30):
                res = await client.get(f"/api/onboarding/check-slug?slug=test-slug-{i}", headers=headers)
                self.assertEqual(res.status_code, 200)

            # 31st request exceeds rate limit -> 429
            blocked_res = await client.get("/api/onboarding/check-slug?slug=test-slug-blocked", headers=headers)
            self.assertEqual(blocked_res.status_code, 429)

    async def test_parse_raw_max_payload_length_validation(self):
        headers = {"Authorization": "Bearer test-valid-auth-token-12345"}
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Valid string within 64KB
            valid_res = await client.post(
                "/api/accounts/parse-raw",
                json={"raw_text": "act_1083480094013618"},
                headers=headers,
            )
            self.assertEqual(valid_res.status_code, 200)
            self.assertEqual(len(valid_res.json()), 1)

            # Oversized string > 65536 characters -> 422 Unprocessable Entity
            oversized_res = await client.post(
                "/api/accounts/parse-raw",
                json={"raw_text": "A" * 70000},
                headers=headers,
            )
            self.assertEqual(oversized_res.status_code, 422)

    async def test_request_body_size_limit_middleware(self):
        # 1MB limit for standard API routes -> HTTP 413
        headers = {
            "Authorization": "Bearer test-valid-auth-token-12345",
            "Content-Length": "2000000",
            "Content-Type": "application/json",
        }
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post(
                "/api/accounts/parse-raw",
                content=b"{}",
                headers=headers,
            )
            self.assertEqual(res.status_code, 413)
            self.assertIn("Размер тела запроса превышает", res.json()["detail"])
