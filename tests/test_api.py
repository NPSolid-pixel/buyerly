import hmac
import hashlib
import json
import urllib.parse
import unittest
import httpx
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from database.db import Base
from database.models import TelegramUser, Account, AppSettings
from core.config import settings
from api.auth import validate_telegram_init_data
from api.server import create_app
import api.routes as api_routes_module
import api.auth as api_auth_module

def generate_valid_telegram_init_data(bot_token: str, user_dict: dict) -> str:
    params = {
        "auth_date": "1723790000",
        "query_id": "AAHdF6IQAAAAAN0XohD9KkG4",
        "user": json.dumps(user_dict, separators=(',', ':'))
    }
    data_check_list = [f"{k}={v}" for k, v in sorted(params.items())]
    data_check_string = "\n".join(data_check_list)
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    hash_val = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    params["hash"] = hash_val
    return urllib.parse.urlencode(params)


class TestWebApi(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        self.test_session_maker = async_sessionmaker(self.test_engine, class_=AsyncSession, expire_on_commit=False)

        async with self.test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Patch session maker in modules
        api_routes_module.async_session_maker = self.test_session_maker
        api_auth_module.async_session_maker = self.test_session_maker

        settings.BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
        settings.ADMIN_CHAT_ID = "8634201356"

        # Populate initial test user & account
        async with self.test_session_maker() as session:
            admin_user = TelegramUser(
                telegram_id="8634201356",
                username="admin_user",
                full_name="Admin Test",
                role="admin",
                is_approved=True
            )
            session.add(admin_user)

            buyer_user = TelegramUser(
                telegram_id="8948797431",
                username="buyer_nick",
                full_name="Buyer Nick",
                role="buyer",
                is_approved=True
            )
            session.add(buyer_user)

            acc = Account(
                account_id="act_1018756607700064",
                name="Швеция 1",
                access_token="mock_token",
                owner_id="8948797431",
                timezone_name="UTC",
                max_spend_0_leads=2.0,
                max_spend_1_lead=6.0,
                max_cpa_multiple_leads=6.0,
                rules_enabled=False,
                is_active=True
            )
            session.add(acc)

            app_set = AppSettings(poll_interval_minutes=15)
            session.add(app_set)

            await session.commit()

        self.app = create_app()

    async def asyncTearDown(self):
        await self.test_engine.dispose()

    def test_init_data_validation(self):
        user_info = {"id": 8948797431, "first_name": "Nick", "username": "buyer_nick"}
        valid_init_data = generate_valid_telegram_init_data(settings.BOT_TOKEN, user_info)
        
        # Valid signature
        res = validate_telegram_init_data(valid_init_data, settings.BOT_TOKEN)
        self.assertIsNotNone(res)
        self.assertEqual(res["user"]["id"], 8948797431)

        # Tampered signature
        tampered = valid_init_data.replace("8948797431", "9999999999")
        res_invalid = validate_telegram_init_data(tampered, settings.BOT_TOKEN)
        self.assertIsNone(res_invalid)

    async def test_get_me_and_accounts(self):
        user_info = {"id": 8948797431, "first_name": "Nick", "username": "buyer_nick"}
        init_data = generate_valid_telegram_init_data(settings.BOT_TOKEN, user_info)

        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Authorization": f"tma {init_data}"}

            # /api/me
            me_resp = await client.get("/api/me", headers=headers)
            self.assertEqual(me_resp.status_code, 200)
            me_data = me_resp.json()
            self.assertEqual(me_data["telegram_id"], "8948797431")
            self.assertEqual(me_data["role"], "buyer")

            # /api/accounts
            acc_resp = await client.get("/api/accounts", headers=headers)
            self.assertEqual(acc_resp.status_code, 200)
            accounts = acc_resp.json()
            self.assertEqual(len(accounts), 1)
            self.assertEqual(accounts[0]["account_id"], "act_1018756607700064")
            self.assertEqual(accounts[0]["rules_enabled"], False)

    async def test_toggle_rules_and_limits(self):
        user_info = {"id": 8948797431, "first_name": "Nick", "username": "buyer_nick"}
        init_data = generate_valid_telegram_init_data(settings.BOT_TOKEN, user_info)

        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Authorization": f"tma {init_data}"}

            # Toggle rules -> ON
            t_resp = await client.post("/api/accounts/act_1018756607700064/toggle-rules", headers=headers)
            self.assertEqual(t_resp.status_code, 200)
            self.assertTrue(t_resp.json()["rules_enabled"])

            # Update limits
            limits_payload = {
                "max_spend_0_leads": 3.5,
                "max_spend_1_lead": 8.5,
                "max_cpa_multiple_leads": 8.5,
                "conversion_event": "leads",
                "auto_reactivate": True
            }
            l_resp = await client.post("/api/accounts/act_1018756607700064/limits", headers=headers, json=limits_payload)
            self.assertEqual(l_resp.status_code, 200)
            l_data = l_resp.json()
            self.assertEqual(l_data["max_spend_0_leads"], 3.5)
            self.assertEqual(l_data["max_spend_1_lead"], 8.5)
            self.assertEqual(l_data["auto_reactivate"], True)

    async def test_parse_raw_endpoint(self):
        raw_fb_text = """
        Ad account ID: 1083480094013618
        Швеция 1083
        act_1070862758952340
        """
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/accounts/parse-raw", json={"raw_text": raw_fb_text})
            self.assertEqual(resp.status_code, 200)
            items = resp.json()
            self.assertEqual(len(items), 2)
            self.assertEqual(items[0]["account_id"], "act_1083480094013618")
            self.assertEqual(items[1]["account_id"], "act_1070862758952340")

    async def test_settings_endpoint(self):
        admin_info = {"id": 8634201356, "first_name": "Admin", "username": "admin_user"}
        admin_init_data = generate_valid_telegram_init_data(settings.BOT_TOKEN, admin_info)

        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Authorization": f"tma {admin_init_data}"}

            # Get settings
            s_resp = await client.get("/api/settings", headers=headers)
            self.assertEqual(s_resp.status_code, 200)
            self.assertEqual(s_resp.json()["poll_interval_minutes"], 15)

            # Change interval to 30 min
            set_resp = await client.post("/api/settings/interval", headers=headers, json={"minutes": 30})
            self.assertEqual(set_resp.status_code, 200)
            self.assertEqual(set_resp.json()["poll_interval_minutes"], 30)

    async def test_delete_account(self):
        user_info = {"id": 8948797431, "first_name": "Nick", "username": "buyer_nick"}
        init_data = generate_valid_telegram_init_data(settings.BOT_TOKEN, user_info)

        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Authorization": f"tma {init_data}"}

            del_resp = await client.delete("/api/accounts/act_1018756607700064", headers=headers)
            self.assertEqual(del_resp.status_code, 200)

            # Verify it's gone
            acc_resp = await client.get("/api/accounts", headers=headers)
            self.assertEqual(len(acc_resp.json()), 0)
