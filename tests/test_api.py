import hashlib
import hmac
import json
import time
import unittest
import urllib.parse
from unittest.mock import AsyncMock, patch

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import api.auth as api_auth_module
import api.routes as api_routes_module
import api.server as api_server_module
from api.auth import validate_telegram_init_data
from api.server import create_app
from core.config import settings
from database.db import Base, hash_password, verify_password
from database.models import (
    Account,
    AccountGroup,
    AccountGroupMember,
    AppSettings,
    AuditEvent,
    EmailVerificationCode,
    RuleGroup,
    RuleGroupItem,
    RulePreset,
    SummarySnapshot,
    AnalyticsViewPreference,
    MetaConnection,
    StoppedAdSet,
    User,
    Workspace,
    WorkspaceMember,
)


from tests.test_db_helper import create_test_engine, init_test_db


def generate_valid_telegram_init_data(bot_token: str, user_dict: dict, auth_date: int = None) -> str:
    if auth_date is None:
        auth_date = int(time.time())
    user_str = json.dumps(user_dict, separators=(",", ":"), ensure_ascii=False)
    params = {
        "auth_date": str(auth_date),
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": user_str
    }
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    hash_val = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    params["hash"] = hash_val
    return urllib.parse.urlencode(params)


class TestWebApi(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        api_routes_module._summary_cache.clear()
        self.test_engine = create_test_engine()
        self.test_session_maker = async_sessionmaker(self.test_engine, class_=AsyncSession, expire_on_commit=False)
        await init_test_db(self.test_engine)

        # Patch session maker in modules
        api_routes_module.async_session_maker = self.test_session_maker
        api_auth_module.async_session_maker = self.test_session_maker
        api_server_module.async_session_maker = self.test_session_maker

        settings.BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
        settings.ADMIN_CHAT_ID = "8634201356"

        # Populate initial test user & account
        async with self.test_session_maker() as session:
            admin_user = User(
                telegram_id="8634201356",
                username="admin_user",
                full_name="Admin Test",
                password_hash=hash_password("admin-password"),
                role="admin",
                is_approved=True,
            )
            session.add(admin_user)

            buyer_user = User(
                telegram_id="8948797431",
                username="buyer_nick",
                full_name="Buyer Nick",
                role="buyer",
                is_approved=True,
            )
            session.add(buyer_user)
            await session.flush()

            ws_admin = Workspace(
                name="Admin Workspace",
                slug="admin-workspace",
                badge_text="A",
                badge_color="#3B82F6",
                owner_user_id=admin_user.id,
            )
            ws_buyer = Workspace(
                name="Buyer Workspace",
                slug="buyer-workspace",
                badge_text="B",
                badge_color="#10B981",
                owner_user_id=buyer_user.id,
            )
            session.add_all([ws_admin, ws_buyer])
            await session.flush()

            session.add(WorkspaceMember(workspace_id=ws_admin.id, user_id=admin_user.id, role="owner"))
            session.add(WorkspaceMember(workspace_id=ws_buyer.id, user_id=buyer_user.id, role="owner"))
            admin_user.active_workspace_id = ws_admin.id
            buyer_user.active_workspace_id = ws_buyer.id

            acc = Account(
                account_id="act_1018756607700064",
                name="Швеция 1",
                access_token="mock_token",
                owner_user_id=buyer_user.id,
                workspace_id=ws_buyer.id,
                timezone_name="UTC",
                currency="USD",
                rules_enabled=False,
                is_active=True,
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
        now = 2_000_000_000
        valid_init_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            user_info,
            auth_date=now,
        )
        
        # Valid signature
        res = validate_telegram_init_data(valid_init_data, settings.BOT_TOKEN, now=now)
        self.assertIsNotNone(res)
        self.assertEqual(res["user"]["id"], 8948797431)

        # Tampered signature
        tampered_init_data = valid_init_data.replace("buyer_nick", "hacker")
        tampered_res = validate_telegram_init_data(
            tampered_init_data,
            settings.BOT_TOKEN,
            now=now,
        )
        self.assertIsNone(tampered_res)

        stale_init_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            user_info,
            auth_date=now - 86401,
        )
        stale_res = validate_telegram_init_data(
            stale_init_data,
            settings.BOT_TOKEN,
            now=now,
            max_age_seconds=86400,
        )
        self.assertIsNone(stale_res)

        future_init_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            user_info,
            auth_date=now + 61,
        )
        future_res = validate_telegram_init_data(
            future_init_data,
            settings.BOT_TOKEN,
            now=now,
        )
        self.assertIsNone(future_res)

    async def test_health_endpoints(self):
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            live_response = await client.get("/health/live")
            ready_response = await client.get("/health/ready")

        self.assertEqual(live_response.status_code, 200)
        self.assertEqual(live_response.json()["status"], "alive")
        self.assertEqual(ready_response.status_code, 200)
        self.assertEqual(ready_response.json()["status"], "ready")
        self.assertIn("version", ready_response.json())

    async def test_password_login_upgrades_legacy_hash(self):
        legacy_password = "legacy-password"
        async with self.test_session_maker() as session:
            result = await session.execute(
                select(User).where(User.username == "buyer_nick")
            )
            buyer = result.scalar_one()
            buyer.password_hash = hashlib.sha256(legacy_password.encode()).hexdigest()
            await session.commit()

        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            missing_password = await client.post(
                "/api/auth/login",
                json={"username": "admin_user", "password": "anything"},
            )
            wrong_password = await client.post(
                "/api/auth/login",
                json={"username": "buyer_nick", "password": "wrong-password"},
            )
            login = await client.post(
                "/api/auth/login",
                json={"username": "buyer_nick", "password": legacy_password},
            )

        self.assertEqual(missing_password.status_code, 401)
        self.assertEqual(wrong_password.status_code, 401)
        self.assertEqual(login.status_code, 200)
        self.assertTrue(login.json()["token"])

        async with self.test_session_maker() as session:
            result = await session.execute(
                select(User).where(User.username == "buyer_nick")
            )
            upgraded_buyer = result.scalar_one()
            self.assertTrue(upgraded_buyer.password_hash.startswith("pbkdf2_sha256$"))
            self.assertTrue(verify_password(legacy_password, upgraded_buyer.password_hash))

    async def test_change_password_requires_current_password(self):
        old_password = "old-password"
        new_password = "new-password"
        async with self.test_session_maker() as session:
            result = await session.execute(
                select(User).where(User.username == "buyer_nick")
            )
            buyer = result.scalar_one()
            buyer.password_hash = hashlib.sha256(old_password.encode()).hexdigest()
            buyer.auth_token = "test-web-token"
            await session.commit()

        headers = {"Authorization": "Bearer test-web-token"}
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            missing_current = await client.post(
                "/api/auth/change-password",
                headers=headers,
                json={"new_password": new_password},
            )
            wrong_current = await client.post(
                "/api/auth/change-password",
                headers=headers,
                json={"old_password": "wrong-password", "new_password": new_password},
            )
            changed = await client.post(
                "/api/auth/change-password",
                headers=headers,
                json={"old_password": old_password, "new_password": new_password},
            )

        self.assertEqual(missing_current.status_code, 400)
        self.assertEqual(wrong_current.status_code, 400)
        self.assertEqual(changed.status_code, 200)

        async with self.test_session_maker() as session:
            result = await session.execute(
                select(User).where(User.username == "buyer_nick")
            )
            changed_buyer = result.scalar_one()
            self.assertTrue(verify_password(new_password, changed_buyer.password_hash))

    async def test_telegram_id_change_keeps_all_data_owned_by_the_same_user(self):
        old_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            {"id": 8948797431, "first_name": "Nick", "username": "buyer_nick"},
        )
        old_headers = {"Authorization": f"tma {old_data}"}
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            preset_response = await client.post(
                "/api/presets",
                headers=old_headers,
                json={
                    "name": "Stable owner rule",
                    "action": "notify_only",
                    "conditions": [
                        {"metric": "spend", "operator": "gte", "value": 10, "time_window": "today"}
                    ],
                    "condition_logic": "and",
                },
            )
            self.assertEqual(preset_response.status_code, 200)
            preset_id = preset_response.json()["id"]
            group_response = await client.post(
                "/api/rule-groups",
                headers=old_headers,
                json={"name": "Stable group", "description": "", "preset_ids": [preset_id]},
            )
            self.assertEqual(group_response.status_code, 200)
            view_response = await client.put(
                "/api/analytics-view",
                headers=old_headers,
                json={"view_mode": "traffic", "visible_columns": ["account", "data", "clicks"]},
            )
            self.assertEqual(view_response.status_code, 200)
            update_response = await client.post(
                "/api/auth/update-profile",
                headers=old_headers,
                json={"telegram_id": "9000000001"},
            )
            self.assertEqual(update_response.status_code, 200)

            new_data = generate_valid_telegram_init_data(
                settings.BOT_TOKEN,
                {"id": 9000000001, "first_name": "Nick", "username": "buyer_nick"},
            )
            new_headers = {"Authorization": f"tma {new_data}"}
            accounts = await client.get("/api/accounts", headers=new_headers)
            presets = await client.get("/api/presets", headers=new_headers)
            groups = await client.get("/api/rule-groups", headers=new_headers)
            view = await client.get("/api/analytics-view", headers=new_headers)

        self.assertEqual(accounts.status_code, 200)
        self.assertEqual(len(accounts.json()), 1)
        self.assertIn(preset_id, [item["id"] for item in presets.json()])
        self.assertIn("Stable group", [item["name"] for item in groups.json()])
        self.assertEqual(view.json()["view_mode"], "traffic")

        async with self.test_session_maker() as session:
            user = (
                await session.execute(
                    select(User).where(User.telegram_id == "9000000001")
                )
            ).scalar_one()
            account = (
                await session.execute(
                    select(Account).where(Account.account_id == "act_1018756607700064")
                )
            ).scalar_one()
            self.assertEqual(account.owner_user_id, user.id)
            # verified owner_user_id

    async def test_get_accounts_endpoint(self):
        user_info = {"id": 8948797431, "first_name": "Nick", "username": "buyer_nick"}
        init_data = generate_valid_telegram_init_data(settings.BOT_TOKEN, user_info)

        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Authorization": f"tma {init_data}"}
            resp = await client.get("/api/accounts", headers=headers)
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["account_id"], "act_1018756607700064")
            self.assertEqual(data[0]["name"], "Швеция 1")
            self.assertEqual(data[0]["custom_name"], "")
            self.assertEqual(data[0]["note"], "")
            self.assertEqual(data[0]["group_ids"], [])
            self.assertEqual(data[0]["connection_type"], "system_user")
            self.assertIsNone(data[0]["latest_metrics"])
            self.assertEqual(data[0]["active_rules"], [])

    async def test_account_groups_are_crud_owner_scoped_and_exposed_on_accounts(self):
        async with self.test_session_maker() as session:
            buyer = (await session.execute(select(User).where(User.telegram_id == "8948797431"))).scalar_one()
            admin = (await session.execute(select(User).where(User.telegram_id == "8634201356"))).scalar_one()
            session.add_all(
                [
                    Account(
                        account_id="act_2000000000000001",
                        name="NL second",
                        access_token="mock_token",
                        owner_user_id=buyer.id,
                        workspace_id=buyer.active_workspace_id,
                        timezone_name="UTC",
                        currency="USD",
                    ),
                    Account(
                        account_id="act_9000000000000001",
                        name="Admin foreign",
                        access_token="mock_token",
                        owner_user_id=admin.id,
                        workspace_id=admin.active_workspace_id,
                        timezone_name="UTC",
                        currency="USD",
                    ),
                ]
            )
            await session.commit()

        buyer_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            {"id": 8948797431, "first_name": "Nick", "username": "buyer_nick"},
        )
        admin_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            {"id": 8634201356, "first_name": "Admin", "username": "admin_user"},
        )
        buyer_headers = {"Authorization": f"tma {buyer_data}"}
        admin_headers = {"Authorization": f"tma {admin_data}"}
        transport = httpx.ASGITransport(app=self.app)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/api/account-groups",
                headers=buyer_headers,
                json={
                    "name": "  NL · основной  ",
                    "description": "  Нидерланды, основной оффер  ",
                    "account_ids": ["act_2000000000000001", "act_1018756607700064"],
                },
            )
            self.assertEqual(created.status_code, 201)
            group = created.json()
            self.assertEqual(group["name"], "NL · основной")
            self.assertEqual(group["description"], "Нидерланды, основной оффер")
            self.assertEqual(
                group["account_ids"],
                ["act_2000000000000001", "act_1018756607700064"],
            )
            self.assertEqual(group["accounts_count"], 2)

            duplicate = await client.post(
                "/api/account-groups",
                headers=buyer_headers,
                json={"name": "nl · ОСНОВНОЙ", "account_ids": []},
            )
            self.assertEqual(duplicate.status_code, 409)

            foreign_member = await client.post(
                "/api/account-groups",
                headers=buyer_headers,
                json={"name": "Invalid", "account_ids": ["act_9000000000000001"]},
            )
            self.assertEqual(foreign_member.status_code, 422)

            buyer_groups = await client.get("/api/account-groups", headers=buyer_headers)
            admin_groups = await client.get("/api/account-groups", headers=admin_headers)
            accounts = await client.get("/api/accounts", headers=buyer_headers)
            self.assertEqual(len(buyer_groups.json()), 1)
            self.assertEqual(admin_groups.json(), [])
            grouped_accounts = {
                item["account_id"]: item["group_ids"] for item in accounts.json()
            }
            self.assertEqual(grouped_accounts["act_1018756607700064"], [group["id"]])
            self.assertEqual(grouped_accounts["act_2000000000000001"], [group["id"]])

            forbidden_update = await client.put(
                f"/api/account-groups/{group['id']}",
                headers=admin_headers,
                json={"name": "Hijack", "account_ids": []},
            )
            self.assertEqual(forbidden_update.status_code, 404)

            updated = await client.put(
                f"/api/account-groups/{group['id']}",
                headers=buyer_headers,
                json={
                    "name": "NL · масштабирование",
                    "description": "",
                    "account_ids": ["act_1018756607700064"],
                },
            )
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.json()["accounts_count"], 1)
            self.assertEqual(updated.json()["account_ids"], ["act_1018756607700064"])

            account_deleted = await client.delete(
                "/api/accounts/act_1018756607700064",
                headers=buyer_headers,
            )
            self.assertEqual(account_deleted.status_code, 200)
            group_after_account_delete = await client.get(
                "/api/account-groups",
                headers=buyer_headers,
            )
            self.assertEqual(group_after_account_delete.json()[0]["accounts_count"], 0)
            self.assertEqual(group_after_account_delete.json()[0]["account_ids"], [])

            deleted = await client.delete(
                f"/api/account-groups/{group['id']}",
                headers=buyer_headers,
            )
            self.assertEqual(deleted.status_code, 200)
            self.assertEqual(
                (await client.get("/api/account-groups", headers=buyer_headers)).json(),
                [],
            )

        async with self.test_session_maker() as session:
            self.assertEqual(
                int((await session.execute(select(func.count()).select_from(AccountGroup))).scalar_one()),
                0,
            )
            self.assertEqual(
                int((await session.execute(select(func.count()).select_from(AccountGroupMember))).scalar_one()),
                0,
            )

    async def test_account_profile_and_latest_saved_metrics_are_owner_isolated(self):
        async with self.test_session_maker() as session:
            buyer = (
                await session.execute(
                    select(User).where(User.telegram_id == "8948797431")
                )
            ).scalar_one()
            conn_obj = MetaConnection(
                owner_user_id=buyer.id,
                provider_user_id="provider_nick_1",
                access_token_encrypted="encrypted_token",
                status="active",
            )
            session.add(conn_obj)
            await session.flush()
            account = (
                await session.execute(
                    select(Account).where(Account.account_id == "act_1018756607700064")
                )
            ).scalar_one()
            account.owner_user_id = buyer.id
            account.meta_connection_id = conn_obj.id
            session.add(
                SummarySnapshot(
                    owner_user_id=buyer.id,
                    period="today",
                    payload={
                        "period": "today",
                        "generated_at": "2026-08-18T08:15:00+00:00",
                        "accounts": [
                            {
                                "account_id": account.account_id,
                                "data_status": "synced",
                                "data_status_label": "Метрики получены",
                                "spend": 123.45,
                                "impressions": 9000,
                                "clicks": 210,
                                "leads": 17,
                                "registrations": 6,
                                "purchases": 2,
                            }
                        ],
                    },
                )
            )
            account_group = AccountGroup(
                owner_user_id=buyer.id,
                name="NL",
                description="Netherlands",
            )
            session.add(account_group)
            await session.flush()
            session.add(AccountGroupMember(group_id=account_group.id, account_id=account.id, position=0))
            admin = (await session.execute(select(User).where(User.telegram_id == "8634201356"))).scalar_one()
            session.add(
                Account(
                    account_id="act_999999999",
                    name="Foreign",
                    owner_user_id=admin.id,
                    workspace_id=admin.active_workspace_id,
                    timezone_name="UTC",
                    currency="USD",
                )
            )
            await session.commit()

        buyer_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            {"id": 8948797431, "first_name": "Nick", "username": "buyer_nick"},
        )
        headers = {"Authorization": f"tma {buyer_data}"}
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            accounts = await client.get("/api/accounts", headers=headers)
            updated = await client.patch(
                "/api/accounts/act_1018756607700064/profile",
                headers=headers,
                json={"custom_name": "  NL · основной  ", "note": "  Льётся NL, новый оффер  "},
            )
            forbidden = await client.patch(
                "/api/accounts/act_999999999/profile",
                headers=headers,
                json={"custom_name": "Hijack", "note": ""},
            )
            invalid = await client.patch(
                "/api/accounts/act_1018756607700064/profile",
                headers=headers,
                json={"custom_name": "x" * 121, "note": ""},
            )
            summary = await client.get("/api/summary?period=today", headers=headers)

        self.assertEqual(accounts.status_code, 200)
        item = accounts.json()[0]
        self.assertEqual(item["connection_type"], "facebook_login")
        self.assertEqual(item["latest_metrics"]["spend"], 123.45)
        self.assertEqual(item["latest_metrics"]["leads"], 17)
        self.assertEqual(item["latest_metrics"]["registrations"], 6)
        self.assertEqual(item["latest_metrics"]["purchases"], 2)
        self.assertEqual(item["latest_metrics"]["generated_at"], "2026-08-18T08:15:00+00:00")
        self.assertTrue(item["latest_metrics"]["saved_at"])
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["custom_name"], "NL · основной")
        self.assertEqual(updated.json()["note"], "Льётся NL, новый оффер")
        self.assertEqual(summary.status_code, 200)
        summary_account = summary.json()["accounts"][0]
        self.assertEqual(summary_account["custom_name"], "NL · основной")
        self.assertEqual(summary_account["note"], "Льётся NL, новый оффер")
        self.assertEqual(summary_account["group_ids"], [account_group.id])
        self.assertEqual(forbidden.status_code, 404)
        self.assertEqual(invalid.status_code, 422)

    async def test_toggle_rules_and_presets(self):
        user_info = {"id": 8948797431, "first_name": "Nick", "username": "buyer_nick"}
        init_data = generate_valid_telegram_init_data(settings.BOT_TOKEN, user_info)

        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Authorization": f"tma {init_data}"}

            invalid_interval = await client.post(
                "/api/presets",
                headers=headers,
                json={
                    "name": "Invalid interval",
                    "conditions": [],
                    "check_interval_minutes": 0,
                },
            )
            self.assertEqual(invalid_interval.status_code, 422)

            removed_cpa = await client.post(
                "/api/presets",
                headers=headers,
                json={
                    "name": "Unsafe combined CPA",
                    "conditions": [
                        {"metric": "cpa", "operator": "gte", "value": 10.0}
                    ],
                },
            )
            self.assertEqual(removed_cpa.status_code, 422)

            base_condition = [
                {"metric": "spend", "operator": "gte", "value": 10.0, "time_window": "today"}
            ]
            invalid_payloads = [
                {"name": "Unknown action", "action": "delete_account", "conditions": base_condition},
                {"name": "Unknown logic", "condition_logic": "xor", "conditions": base_condition},
                {
                    "name": "Unknown window",
                    "conditions": [{**base_condition[0], "time_window": "lifetime"}],
                },
                {
                    "name": "Unsafe percent",
                    "action": "increase_budget",
                    "conditions": base_condition,
                    "budget_change_percent": 500,
                    "budget_max_daily": 100,
                },
                {
                    "name": "Missing ceiling",
                    "action": "increase_budget",
                    "conditions": base_condition,
                    "budget_change_percent": 20,
                    "budget_max_daily": 0,
                },
                {"name": "Negative threshold", "conditions": [{**base_condition[0], "value": -1}]},
                {
                    "name": "Impossible range",
                    "conditions": [
                        {"metric": "spend", "operator": "gte", "value": 10, "time_window": "today"},
                        {"metric": "spend", "operator": "lt", "value": 5, "time_window": "today"},
                    ],
                },
                {
                    "name": "Non-integer count metric",
                    "action": "turn_off",
                    "conditions": [
                        {"metric": "registrations", "operator": "gte", "value": 1.5, "time_window": "today"}
                    ],
                },
            ]
            for invalid_payload in invalid_payloads:
                invalid_response = await client.post(
                    "/api/presets",
                    headers=headers,
                    json=invalid_payload,
                )
                self.assertEqual(invalid_response.status_code, 422, invalid_payload["name"])

            # An account cannot be enabled before at least one rule is attached.
            t_resp = await client.post("/api/accounts/act_1018756607700064/toggle-rules", headers=headers)
            self.assertEqual(t_resp.status_code, 400)

            # Create Preset with OR logic, new metric, and budget scaling
            preset_payload = {
                "name": "Тестовый пресет",
                "action": "increase_budget",
                "condition_logic": "or",
                "budget_change_percent": 25.0,
                "budget_max_daily": 200.0,
                "conditions": [
                    {"metric": "leads", "operator": "gte", "value": 5.0, "time_window": "today"},
                    {"metric": "cpl", "operator": "lt", "value": 3.0, "time_window": "yesterday"}
                ]
            }
            p_resp = await client.post("/api/presets", headers=headers, json=preset_payload)
            self.assertEqual(p_resp.status_code, 200)
            p_data = p_resp.json()
            self.assertEqual(p_data["condition_logic"], "or")
            self.assertEqual(p_data["budget_change_percent"], 25.0)
            self.assertEqual(len(p_data["conditions"]), 2)

            # Assign Preset to Account
            apply_payload = {
                "preset_id": p_data["id"]
            }
            a_resp = await client.post("/api/accounts/act_1018756607700064/assign-rule", headers=headers, json=apply_payload)
            self.assertEqual(a_resp.status_code, 200)
            a_data = a_resp.json()
            self.assertTrue(a_data["rules_enabled"])
            self.assertEqual(len(a_data["active_rules"]), 1)
            assigned_rule = a_data["active_rules"][0]
            self.assertEqual(assigned_rule["action"], "increase_budget")
            self.assertEqual(assigned_rule["logic"], "or")
            self.assertEqual(assigned_rule["budget_change_percent"], 25.0)

            # Updating a preset immediately updates its runtime snapshot.
            updated_payload = {
                **preset_payload,
                "name": "Тестовый пресет v2",
                "action": "turn_off",
                "condition_logic": "and",
                "budget_change_percent": 0.0,
                "budget_max_daily": 0.0,
            }
            u_resp = await client.put(
                f"/api/presets/{p_data['id']}", headers=headers, json=updated_payload
            )
            self.assertEqual(u_resp.status_code, 200)

            accounts_resp = await client.get("/api/accounts", headers=headers)
            runtime_rule = accounts_resp.json()[0]["active_rules"][0]
            self.assertEqual(runtime_rule["name"], "Тестовый пресет v2")
            self.assertEqual(runtime_rule["action"], "turn_off")
            self.assertEqual(runtime_rule["logic"], "and")

            # Detaching is targeted and disables the account when no rules remain.
            d_resp = await client.post(
                f"/api/accounts/act_1018756607700064/detach-rule/{p_data['id']}",
                headers=headers,
            )
            self.assertEqual(d_resp.status_code, 200)
            self.assertEqual(d_resp.json()["active_rules"], [])
            self.assertFalse(d_resp.json()["rules_enabled"])

    async def test_account_rejects_rules_with_opposite_actions_and_same_trigger(self):
        init_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            {"id": 8948797431, "first_name": "Nick", "username": "buyer_nick"},
        )
        headers = {"Authorization": f"tma {init_data}"}
        condition = [
            {"metric": "spend", "operator": "gte", "value": 10, "time_window": "today"}
        ]

        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            stop = await client.post(
                "/api/presets",
                headers=headers,
                json={"name": "Выключить", "action": "turn_off", "conditions": condition},
            )
            start = await client.post(
                "/api/presets",
                headers=headers,
                json={"name": "Включить", "action": "turn_on", "conditions": condition},
            )
            self.assertEqual(stop.status_code, 200)
            self.assertEqual(start.status_code, 200)

            first_assignment = await client.post(
                "/api/accounts/act_1018756607700064/assign-rule",
                headers=headers,
                json={"preset_id": stop.json()["id"]},
            )
            conflict = await client.post(
                "/api/accounts/act_1018756607700064/assign-rule",
                headers=headers,
                json={"preset_id": start.json()["id"]},
            )

        self.assertEqual(first_assignment.status_code, 200)
        self.assertEqual(conflict.status_code, 409)
        self.assertIn("противоречат", conflict.json()["detail"])

        async with self.test_session_maker() as session:
            account = (
                await session.execute(
                    select(Account).where(Account.account_id == "act_1018756607700064")
                )
            ).scalar_one()
        self.assertEqual(len(json.loads(account.active_rules)), 1)

    async def test_rule_groups_are_isolated_editable_and_assigned_atomically(self):
        async with self.test_session_maker() as session:
            buyer = (await session.execute(select(User).where(User.telegram_id == "8948797431"))).scalar_one()
            admin = (await session.execute(select(User).where(User.telegram_id == "8634201356"))).scalar_one()
            buyer_presets = [
                RulePreset(
                    owner_user_id=buyer.id,
                    workspace_id=buyer.active_workspace_id,
                    name="Stop no leads",
                    action="turn_off",
                    conditions=[{"metric": "spend", "operator": "gte", "value": 10}],
                ),
                RulePreset(
                    owner_user_id=buyer.id,
                    workspace_id=buyer.active_workspace_id,
                    name="Notify high CPL",
                    action="notify_only",
                    conditions=[{"metric": "cpl", "operator": "gte", "value": 7}],
                ),
            ]
            foreign_preset = RulePreset(
                owner_user_id=admin.id,
                workspace_id=admin.active_workspace_id,
                name="Admin private rule",
                action="turn_off",
                conditions=[],
            )
            session.add_all([*buyer_presets, foreign_preset])
            await session.commit()
            for preset in [*buyer_presets, foreign_preset]:
                await session.refresh(preset)
            buyer_ids = [preset.id for preset in buyer_presets]
            foreign_id = foreign_preset.id

        buyer_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            {"id": 8948797431, "first_name": "Nick", "username": "buyer_nick"},
        )
        admin_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            {"id": 8634201356, "first_name": "Admin", "username": "admin_user"},
        )
        buyer_headers = {"Authorization": f"tma {buyer_data}"}
        admin_headers = {"Authorization": f"tma {admin_data}"}
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            foreign_response = await client.post(
                "/api/rule-groups",
                headers=buyer_headers,
                json={"name": "Invalid", "preset_ids": [foreign_id]},
            )
            created = await client.post(
                "/api/rule-groups",
                headers=buyer_headers,
                json={
                    "name": "  Launch safety  ",
                    "description": "Start-of-day protection",
                    "preset_ids": [buyer_ids[0], buyer_ids[1], buyer_ids[0]],
                },
            )

            self.assertEqual(foreign_response.status_code, 400)
            self.assertEqual(created.status_code, 200)
            group = created.json()
            self.assertEqual(group["name"], "Launch safety")
            self.assertEqual(group["preset_ids"], buyer_ids)
            self.assertEqual([rule["name"] for rule in group["rules"]], ["Stop no leads", "Notify high CPL"])

            empty_created = await client.post(
                "/api/rule-groups",
                headers=buyer_headers,
                json={"name": "Empty Stage", "preset_ids": []},
            )
            self.assertEqual(empty_created.status_code, 200)
            self.assertEqual(empty_created.json()["name"], "Empty Stage")
            self.assertEqual(empty_created.json()["preset_ids"], [])
            self.assertEqual(empty_created.json()["rules"], [])

            group_id = group["id"]
            buyer_list = await client.get("/api/rule-groups", headers=buyer_headers)
            admin_list = await client.get("/api/rule-groups", headers=admin_headers)
            forbidden_update = await client.put(
                f"/api/rule-groups/{group_id}",
                headers=admin_headers,
                json={"name": "Hijack", "preset_ids": [foreign_id]},
            )
            self.assertIn(group_id, [item["id"] for item in buyer_list.json()])
            self.assertEqual(
                len([item for item in admin_list.json() if item["name"].startswith("Пример ·")]),
                2,
            )
            self.assertEqual(forbidden_update.status_code, 404)

            single_assign = await client.post(
                "/api/accounts/act_1018756607700064/assign-rule",
                headers=buyer_headers,
                json={"preset_id": buyer_ids[0]},
            )
            grouped_assign = await client.post(
                f"/api/accounts/act_1018756607700064/assign-rule-group/{group_id}",
                headers=buyer_headers,
            )
            repeated_assign = await client.post(
                f"/api/accounts/act_1018756607700064/assign-rule-group/{group_id}",
                headers=buyer_headers,
            )
            self.assertEqual(single_assign.status_code, 200)
            self.assertEqual(grouped_assign.status_code, 200)
            self.assertEqual(grouped_assign.json()["added_count"], 1)
            self.assertEqual(grouped_assign.json()["skipped_count"], 1)
            self.assertEqual(len(grouped_assign.json()["active_rules"]), 2)
            self.assertEqual(repeated_assign.json()["added_count"], 0)
            self.assertEqual(repeated_assign.json()["skipped_count"], 2)

            updated = await client.put(
                f"/api/rule-groups/{group_id}",
                headers=buyer_headers,
                json={"name": "Launch bundle", "description": "Updated", "preset_ids": list(reversed(buyer_ids))},
            )
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.json()["preset_ids"], list(reversed(buyer_ids)))

            deleted = await client.delete(f"/api/rule-groups/{group_id}", headers=buyer_headers)
            accounts = await client.get("/api/accounts", headers=buyer_headers)
            self.assertEqual(deleted.status_code, 200)
            self.assertEqual(len(accounts.json()[0]["active_rules"]), 2)

        async with self.test_session_maker() as session:
            self.assertIsNone(await session.get(RuleGroup, group_id))
            group_items = (
                await session.execute(select(RuleGroupItem).where(RuleGroupItem.group_id == group_id))
            ).scalars().all()
            self.assertEqual(group_items, [])

    async def test_rule_groups_reorder(self):
        user_info = {"id": 8948797431, "first_name": "Nick", "username": "buyer_nick"}
        init_data = generate_valid_telegram_init_data(settings.BOT_TOKEN, user_info)
        headers = {"Authorization": f"tma {init_data}"}
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp1 = await client.post("/api/rule-groups", headers=headers, json={"name": "Group Alpha", "preset_ids": []})
            self.assertEqual(resp1.status_code, 200)
            g1 = resp1.json()

            resp2 = await client.post("/api/rule-groups", headers=headers, json={"name": "Group Beta", "preset_ids": []})
            self.assertEqual(resp2.status_code, 200)
            g2 = resp2.json()

            resp3 = await client.post("/api/rule-groups", headers=headers, json={"name": "Group Gamma", "preset_ids": []})
            self.assertEqual(resp3.status_code, 200)
            g3 = resp3.json()

            reorder_resp = await client.put(
                "/api/rule-groups/reorder",
                headers=headers,
                json={"group_ids": [g3["id"], g1["id"], g2["id"]]},
            )
            self.assertEqual(reorder_resp.status_code, 200)
            reordered = reorder_resp.json()
            custom_groups = [g for g in reordered if g["id"] in {g1["id"], g2["id"], g3["id"]}]
            self.assertEqual([g["id"] for g in custom_groups], [g3["id"], g1["id"], g2["id"]])

            get_resp = await client.get("/api/rule-groups", headers=headers)
            self.assertEqual(get_resp.status_code, 200)
            get_groups = [g for g in get_resp.json() if g["id"] in {g1["id"], g2["id"], g3["id"]}]
            self.assertEqual([g["id"] for g in get_groups], [g3["id"], g1["id"], g2["id"]])

    async def test_parse_raw_endpoint(self):
        user_info = {"id": 8948797431, "first_name": "Nick", "username": "buyer_nick"}
        init_data = generate_valid_telegram_init_data(settings.BOT_TOKEN, user_info)
        raw_fb_text = """
        Ad account ID: 1083480094013618
        Швеция 1083
        act_1070862758952340
        """
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Authorization": f"tma {init_data}"}
            resp = await client.post("/api/accounts/parse-raw", headers=headers, json={"raw_text": raw_fb_text})
            self.assertEqual(resp.status_code, 200)
            items = resp.json()
            self.assertEqual(len(items), 2)
            self.assertEqual(items[0]["account_id"], "act_1083480094013618")
            self.assertEqual(items[1]["account_id"], "act_1070862758952340")

    async def test_batch_import_never_enables_rules_for_a_new_account(self):
        user_info = {"id": 8948797431, "first_name": "Nick", "username": "buyer_nick"}
        init_data = generate_valid_telegram_init_data(settings.BOT_TOKEN, user_info)
        headers = {"Authorization": f"tma {init_data}"}

        meta_account = {
            "timezone_name": "Europe/Stockholm",
            "name": "Imported account",
            "account_status": 1,
            "status_label": "Активен",
            "currency": "EUR",
        }
        legacy_payload = {
            "accounts": [{"account_id": "act_new_account", "name": "New account"}],
            "batch_name": "-",
            "access_token": "new_mock_token",
            # Older clients may still send this field. It must be ignored.
            "rules_enabled": True,
        }

        transport = httpx.ASGITransport(app=self.app)
        with patch.object(
            api_routes_module.meta_client,
            "get_account_info",
            new=AsyncMock(return_value=meta_account),
        ):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/accounts/batch-add",
                    headers=headers,
                    json=legacy_payload,
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["success_count"], 1)

        async with self.test_session_maker() as session:
            result = await session.execute(
                select(Account).where(Account.account_id == "act_new_account")
            )
            imported = result.scalar_one()
            self.assertFalse(imported.rules_enabled)
            self.assertEqual(imported.active_rules, "[]")
            self.assertEqual(imported.currency, "EUR")

    async def test_manual_reimport_marks_existing_account_as_system_user(self):
        user_info = {"id": 8948797431, "first_name": "Nick", "username": "buyer_nick"}
        init_data = generate_valid_telegram_init_data(settings.BOT_TOKEN, user_info)
        headers = {"Authorization": f"tma {init_data}"}
        account_id = "act_1018756607700064"

        async with self.test_session_maker() as session:
            existing = (
                await session.execute(
                    select(Account).where(Account.account_id == account_id)
                )
            ).scalar_one()
            buyer = (
                await session.execute(
                    select(User).where(User.telegram_id == "8948797431")
                )
            ).scalar_one()
            conn_obj = MetaConnection(
                owner_user_id=buyer.id,
                provider_user_id="provider_nick_reimport",
                access_token_encrypted="encrypted_token",
                status="active",
            )
            session.add(conn_obj)
            await session.flush()
            existing.meta_connection_id = conn_obj.id
            await session.commit()

        meta_account = {
            "timezone_name": "Europe/Stockholm",
            "name": "Reconnected account",
            "account_status": 1,
            "status_label": "Активен",
            "currency": "EUR",
        }
        payload = {
            "accounts": [{"account_id": account_id, "name": "Manual source"}],
            "batch_name": "-",
            "access_token": "replacement_system_user_token",
        }

        transport = httpx.ASGITransport(app=self.app)
        with patch.object(
            api_routes_module.meta_client,
            "get_account_info",
            new=AsyncMock(return_value=meta_account),
        ):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/accounts/batch-add",
                    headers=headers,
                    json=payload,
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["success_count"], 1)

        async with self.test_session_maker() as session:
            reconnected = (
                await session.execute(
                    select(Account).where(Account.account_id == account_id)
                )
            ).scalar_one()
            self.assertIsNone(reconnected.meta_connection_id)
            self.assertEqual(reconnected.access_token, "replacement_system_user_token")
            self.assertEqual(reconnected.currency, "EUR")

    async def test_batch_import_rejects_account_without_supported_meta_timezone(self):
        user_info = {"id": 8948797431, "first_name": "Nick", "username": "buyer_nick"}
        init_data = generate_valid_telegram_init_data(settings.BOT_TOKEN, user_info)
        payload = {
            "accounts": [{"account_id": "act_missing_timezone", "name": "Broken clock"}],
            "batch_name": "-",
            "access_token": "new_mock_token",
        }
        meta_account = {
            "timezone_name": "Mars/Olympus_Mons",
            "name": "Broken clock",
            "account_status": 1,
            "status_label": "Активен",
            "currency": "USD",
        }

        transport = httpx.ASGITransport(app=self.app)
        with patch.object(
            api_routes_module.meta_client,
            "get_account_info",
            new=AsyncMock(return_value=meta_account),
        ):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/accounts/batch-add",
                    headers={"Authorization": f"tma {init_data}"},
                    json=payload,
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["success_count"], 0)
        self.assertEqual(response.json()["error_count"], 1)
        self.assertIn("часовой пояс", response.json()["errors"][0]["error"])

        async with self.test_session_maker() as session:
            rejected = (
                await session.execute(
                    select(Account).where(Account.account_id == "act_missing_timezone")
                )
            ).scalar_one_or_none()
            self.assertIsNone(rejected)

    async def test_analytics_view_is_saved_per_user_and_validated(self):
        buyer_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            {"id": 8948797431, "first_name": "Nick", "username": "buyer_nick"},
        )
        admin_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            {"id": 8634201356, "first_name": "Admin", "username": "admin_user"},
        )
        buyer_headers = {"Authorization": f"tma {buyer_data}"}
        admin_headers = {"Authorization": f"tma {admin_data}"}
        transport = httpx.ASGITransport(app=self.app)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            default_view = await client.get("/api/analytics-view", headers=buyer_headers)
            self.assertEqual(default_view.status_code, 200)
            self.assertFalse(default_view.json()["is_saved"])
            self.assertEqual(default_view.json()["view_mode"], "all")
            self.assertEqual(len(default_view.json()["visible_columns"]), 24)
            self.assertEqual(default_view.json()["sort_column"], "")
            self.assertEqual(default_view.json()["filters"], {"query": "", "status": "all", "group_id": "all"})
            self.assertEqual(default_view.json()["period"], "today")

            saved_view = await client.put(
                "/api/analytics-view",
                headers=buyer_headers,
                json={"view_mode": "delivery", "visible_columns": ["spend", "impressions", "cpm"]},
            )
            self.assertEqual(saved_view.status_code, 200)
            self.assertTrue(saved_view.json()["is_saved"])
            self.assertEqual(
                saved_view.json()["visible_columns"],
                ["account", "data", "spend", "impressions", "cpm"],
            )
            self.assertEqual(len(saved_view.json()["column_order"]), 24)
            self.assertEqual(saved_view.json()["column_order"][:3], ["account", "custom_name", "note"])
            self.assertEqual(saved_view.json()["column_widths"]["account"], 260)

            restored_view = await client.get("/api/analytics-view", headers=buyer_headers)
            self.assertEqual(restored_view.json()["view_mode"], "delivery")
            self.assertEqual(restored_view.json()["visible_columns"], saved_view.json()["visible_columns"])

            reordered_view = await client.put(
                "/api/analytics-view",
                headers=buyer_headers,
                json={
                    "view_mode": "custom",
                    "visible_columns": ["account", "data", "spend", "cpp"],
                    "column_order": ["spend", "account", "data", "cpp"],
                    "column_widths": {"spend": 176, "account": 320, "cpp": 88},
                    "sort_column": "spend",
                    "sort_direction": "desc",
                    "filters": {"query": "sweden", "status": "synced", "group_id": "42"},
                    "period": "last_7d",
                },
            )
            self.assertEqual(reordered_view.status_code, 200)
            self.assertEqual(
                reordered_view.json()["column_order"][:4],
                ["spend", "account", "data", "cpp"],
            )
            self.assertEqual(reordered_view.json()["column_widths"]["spend"], 176)
            self.assertEqual(reordered_view.json()["column_widths"]["account"], 320)
            self.assertEqual(reordered_view.json()["sort_column"], "spend")
            self.assertEqual(reordered_view.json()["sort_direction"], "desc")
            self.assertEqual(
                reordered_view.json()["filters"],
                {"query": "sweden", "status": "synced", "group_id": "42"},
            )
            self.assertEqual(reordered_view.json()["period"], "last_7d")

            restored_reordered_view = await client.get("/api/analytics-view", headers=buyer_headers)
            self.assertEqual(restored_reordered_view.json()["column_order"][:4], ["spend", "account", "data", "cpp"])
            self.assertEqual(restored_reordered_view.json()["column_widths"]["cpp"], 88)
            self.assertEqual(restored_reordered_view.json()["sort_column"], "spend")
            self.assertEqual(restored_reordered_view.json()["filters"]["status"], "synced")
            self.assertEqual(restored_reordered_view.json()["period"], "last_7d")

            isolated_admin_view = await client.get("/api/analytics-view", headers=admin_headers)
            self.assertFalse(isolated_admin_view.json()["is_saved"])
            self.assertEqual(len(isolated_admin_view.json()["visible_columns"]), 24)

            invalid_view = await client.put(
                "/api/analytics-view",
                headers=buyer_headers,
                json={"view_mode": "custom", "visible_columns": ["account", "secret_token"]},
            )
            self.assertEqual(invalid_view.status_code, 422)

            invalid_order = await client.put(
                "/api/analytics-view",
                headers=buyer_headers,
                json={"view_mode": "custom", "column_order": ["account", "unknown_metric"]},
            )
            self.assertEqual(invalid_order.status_code, 422)

            invalid_width = await client.put(
                "/api/analytics-view",
                headers=buyer_headers,
                json={"view_mode": "custom", "column_widths": {"spend": 421}},
            )
            self.assertEqual(invalid_width.status_code, 422)

            invalid_sort = await client.put(
                "/api/analytics-view",
                headers=buyer_headers,
                json={"sort_column": "unknown_metric"},
            )
            self.assertEqual(invalid_sort.status_code, 422)

            invalid_filter = await client.put(
                "/api/analytics-view",
                headers=buyer_headers,
                json={"filters": {"owner": "somebody"}},
            )
            self.assertEqual(invalid_filter.status_code, 422)

            invalid_status = await client.put(
                "/api/analytics-view",
                headers=buyer_headers,
                json={"filters": {"status": "archived"}},
            )
            self.assertEqual(invalid_status.status_code, 422)

            invalid_group = await client.put(
                "/api/analytics-view",
                headers=buyer_headers,
                json={"filters": {"group_id": "foreign"}},
            )
            self.assertEqual(invalid_group.status_code, 422)

            invalid_period = await client.put(
                "/api/analytics-view",
                headers=buyer_headers,
                json={"period": "last_30d"},
            )
            self.assertEqual(invalid_period.status_code, 422)

        async with self.test_session_maker() as session:
            count = int(
                (await session.execute(select(func.count()).select_from(AnalyticsViewPreference))).scalar_one()
            )
            self.assertEqual(count, 1)

    async def test_summary_exposes_metric_definitions_quality_and_cache_provenance(self):
        async with self.test_session_maker() as session:
            buyer = (await session.execute(select(User).where(User.telegram_id == "8948797431"))).scalar_one()
            session.add_all(
                [
                    Account(
                        account_id="act_blocked",
                        name="Blocked account",
                        access_token="blocked_token",
                        owner_user_id=buyer.id,
                        workspace_id=buyer.active_workspace_id,
                        currency="USD",
                        account_status=2,
                        is_active=True,
                    ),
                    Account(
                        account_id="act_sync_error",
                        name="Error account",
                        access_token="error_token",
                        owner_user_id=buyer.id,
                        workspace_id=buyer.active_workspace_id,
                        currency="USD",
                        account_status=1,
                        is_active=True,
                    ),
                ]
            )
            await session.commit()

        async def insights_side_effect(account_id, access_token, date_preset):
            if account_id == "act_sync_error":
                raise RuntimeError("Meta unavailable")
            return {
                "spend": 30.0,
                "clicks": 50,
                "impressions": 1000,
                "reach": 600,
                "unique_clicks": 40,
                "link_clicks": 30,
                "outbound_clicks": 25,
                "landing_page_views": 20,
                "leads": 2,
                "registrations": 1,
                "purchases": 1,
            }

        buyer_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            {"id": 8948797431, "first_name": "Nick", "username": "buyer_nick"},
        )
        headers = {"Authorization": f"tma {buyer_data}"}
        transport = httpx.ASGITransport(app=self.app)
        mocked_insights = AsyncMock(side_effect=insights_side_effect)
        with patch.object(api_routes_module.meta_client, "get_account_insights_summary", new=mocked_insights):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                fresh = await client.get("/api/summary?period=today&force=true", headers=headers)
                cached = await client.get("/api/summary?period=today", headers=headers)

        self.assertEqual(fresh.status_code, 200)
        data = fresh.json()
        self.assertEqual(data["source"], "Meta Marketing API")
        self.assertTrue(data["generated_at"].endswith("Z"))
        self.assertNotIn("total_results", data)
        self.assertNotIn("avg_cost_per_result", data)
        self.assertNotIn("total_conversions", data)
        self.assertEqual(data["total_spend"], 60.0)
        self.assertEqual(data["cost_per_lead"], 15.0)
        self.assertEqual(data["cost_per_registration"], 30.0)
        self.assertEqual(data["cost_per_purchase"], 30.0)
        self.assertEqual(data["avg_ctr"], 5.0)
        self.assertEqual(data["avg_cpc"], 0.6)
        self.assertEqual(data["total_impressions"], 2000)
        self.assertEqual(data["total_reach"], 1200)
        self.assertEqual(data["avg_frequency"], 1.67)
        self.assertEqual(data["avg_cpm"], 30.0)
        self.assertEqual(data["total_unique_clicks"], 80)
        self.assertEqual(data["total_link_clicks"], 60)
        self.assertEqual(data["total_outbound_clicks"], 50)
        self.assertEqual(data["total_landing_page_views"], 40)
        self.assertEqual(data["avg_ctr_link"], 3.0)
        self.assertEqual(data["avg_ctr_outbound"], 2.5)
        self.assertEqual(data["avg_cpc_link"], 1.0)
        self.assertEqual(data["cost_per_landing_page_view"], 1.5)
        self.assertIn("Не складываются", data["metric_definitions"]["leads"])
        self.assertIn("Считаются отдельно", data["metric_definitions"]["registrations"])
        self.assertIn("Считаются отдельно", data["metric_definitions"]["purchases"])
        self.assertIn("независимо от их текущего статуса", data["metric_definitions"]["spend"])
        self.assertEqual(
            data["data_quality"],
            {
                "status": "partial",
                "accounts_total": 3,
                "accounts_synced": 2,
                "accounts_failed": 1,
                "accounts_blocked": 0,
                "metrics_coverage_percent": 66.7,
                "monetary_totals_available": True,
                "currency_issue": "",
            },
        )
        self.assertEqual(data["display_currency"], "USD")
        self.assertFalse(data["mixed_currencies"])
        self.assertEqual(data["currency_totals"][0]["spend"], 60.0)
        by_id = {account["account_id"]: account for account in data["accounts"]}
        self.assertEqual(by_id["act_1018756607700064"]["data_status"], "synced")
        self.assertEqual(by_id["act_blocked"]["data_status"], "synced")
        self.assertTrue(by_id["act_blocked"]["is_banned"])
        self.assertEqual(by_id["act_blocked"]["spend"], 30.0)
        self.assertEqual(by_id["act_sync_error"]["data_status"], "error")
        self.assertFalse(data["cache"]["is_cached"])
        self.assertEqual(data["cache"]["origin"], "live")
        self.assertTrue(data["snapshot"]["persisted"])
        self.assertTrue(cached.json()["cache"]["is_cached"])
        self.assertEqual(cached.json()["cache"]["origin"], "memory")
        self.assertEqual(mocked_insights.await_count, 3)

        async with self.test_session_maker() as session:
            snapshot_count = (
                await session.execute(select(func.count()).select_from(SummarySnapshot))
            ).scalar_one()
        self.assertEqual(snapshot_count, 1)

    async def test_summary_survives_reload_and_keeps_previous_snapshot(self):
        buyer_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            {"id": 8948797431, "first_name": "Nick", "username": "buyer_nick"},
        )
        headers = {"Authorization": f"tma {buyer_data}"}
        transport = httpx.ASGITransport(app=self.app)

        first_metrics = {
            "spend": 100.0,
            "clicks": 50,
            "impressions": 1000,
            "leads": 10,
            "registrations": 5,
            "purchases": 1,
        }
        second_metrics = {**first_metrics, "spend": 125.0, "clicks": 60}

        with patch.object(
            api_routes_module.meta_client,
            "get_account_insights_summary",
            new=AsyncMock(side_effect=[first_metrics, second_metrics]),
        ) as mocked_insights:
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                first = await client.get(
                    "/api/summary?period=today&force=true",
                    headers=headers,
                )
                api_routes_module._summary_cache.clear()
                restored = await client.get(
                    "/api/summary?period=today",
                    headers=headers,
                )
                second = await client.get(
                    "/api/summary?period=today&force=true",
                    headers=headers,
                )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(restored.json()["total_spend"], 100.0)
        self.assertEqual(restored.json()["cache"]["origin"], "database")
        self.assertTrue(restored.json()["cache"]["is_cached"])
        self.assertEqual(second.json()["total_spend"], 125.0)
        self.assertEqual(second.json()["snapshot"]["previous"]["total_spend"], 100.0)
        self.assertEqual(mocked_insights.await_count, 2)

        async with self.test_session_maker() as session:
            snapshots = (
                await session.execute(
                    select(SummarySnapshot).order_by(SummarySnapshot.id)
                )
            ).scalars().all()
        self.assertEqual(len(snapshots), 2)
        self.assertNotIn("access_token", snapshots[0].payload)

        with patch.object(
            api_routes_module.meta_client,
            "get_account_insights_summary",
            new=AsyncMock(side_effect=RuntimeError("Meta unavailable")),
        ):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                failed_refresh = await client.get(
                    "/api/summary?period=today&force=true",
                    headers=headers,
                )

        self.assertEqual(failed_refresh.status_code, 502)
        self.assertIn("снимок не изменён", failed_refresh.json()["detail"])
        async with self.test_session_maker() as session:
            snapshot_count_after_failure = (
                await session.execute(select(func.count()).select_from(SummarySnapshot))
            ).scalar_one()
        self.assertEqual(snapshot_count_after_failure, 2)

    async def test_summary_never_combines_money_from_different_currencies(self):
        async with self.test_session_maker() as session:
            buyer = (await session.execute(select(User).where(User.telegram_id == "8948797431"))).scalar_one()
            session.add(
                Account(
                    account_id="act_eur",
                    name="Euro account",
                    access_token="eur_token",
                    owner_user_id=buyer.id,
                    workspace_id=buyer.active_workspace_id,
                    currency="EUR",
                    is_active=True,
                )
            )
            await session.commit()

        async def insights(account_id, access_token, date_preset):
            return {
                "spend": 100.0 if account_id == "act_eur" else 50.0,
                "clicks": 10,
                "impressions": 1000,
                "leads": 2,
                "registrations": 1,
                "purchases": 0,
            }

        buyer_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            {"id": 8948797431, "first_name": "Nick", "username": "buyer_nick"},
        )
        with patch.object(
            api_routes_module.meta_client,
            "get_account_insights_summary",
            new=AsyncMock(side_effect=insights),
        ):
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get(
                    "/api/summary?period=today&force=true",
                    headers={"Authorization": f"tma {buyer_data}"},
                )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["mixed_currencies"])
        self.assertEqual(data["display_currency"], "")
        self.assertIsNone(data["total_spend"])
        self.assertIsNone(data["cost_per_lead"])
        self.assertEqual(
            {item["currency"]: item["spend"] for item in data["currency_totals"]},
            {"EUR": 100.0, "USD": 50.0},
        )


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
            self.assertEqual(s_resp.json()["critical_rule_interval_minutes"], 2)
            self.assertEqual(s_resp.json()["stop_confirmation_minutes"], 10)
            self.assertEqual(s_resp.json()["usage_hard_limit_percent"], 80)

            missing_password = await client.post(
                "/api/settings/interval",
                headers=headers,
                json={"minutes": 30},
            )
            wrong_password = await client.post(
                "/api/settings/interval",
                headers=headers,
                json={"minutes": 30, "current_password": "wrong"},
            )
            set_resp = await client.post(
                "/api/settings/interval",
                headers=headers,
                json={"minutes": 30, "current_password": "admin-password"},
            )

            automation_resp = await client.post(
                "/api/settings/automation",
                headers=headers,
                json={
                    "current_password": "admin-password",
                    "poll_interval_minutes": 15,
                    "critical_rule_interval_minutes": 1,
                    "stop_confirmation_minutes": 7,
                    "inventory_cache_minutes": 5,
                    "account_health_interval_minutes": 30,
                    "max_concurrent_accounts": 2,
                    "max_concurrent_actions": 4,
                    "usage_soft_limit_percent": 55,
                    "usage_hard_limit_percent": 78,
                    "adaptive_polling_enabled": True,
                },
            )

            invalid_thresholds = await client.post(
                "/api/settings/automation",
                headers=headers,
                json={
                    "current_password": "admin-password",
                    "poll_interval_minutes": 15,
                    "critical_rule_interval_minutes": 1,
                    "stop_confirmation_minutes": 7,
                    "inventory_cache_minutes": 5,
                    "account_health_interval_minutes": 30,
                    "max_concurrent_accounts": 2,
                    "max_concurrent_actions": 4,
                    "usage_soft_limit_percent": 80,
                    "usage_hard_limit_percent": 70,
                    "adaptive_polling_enabled": True,
                },
            )

            refreshed = await client.get("/api/settings", headers=headers)

        self.assertEqual(missing_password.status_code, 422)
        self.assertEqual(wrong_password.status_code, 403)
        self.assertEqual(set_resp.status_code, 200)
        self.assertEqual(set_resp.json()["poll_interval_minutes"], 30)
        self.assertEqual(automation_resp.status_code, 200)
        self.assertEqual(invalid_thresholds.status_code, 422)
        self.assertEqual(refreshed.json()["critical_rule_interval_minutes"], 1)
        self.assertEqual(refreshed.json()["stop_confirmation_minutes"], 7)
        self.assertEqual(refreshed.json()["max_concurrent_actions"], 4)
        self.assertEqual(refreshed.json()["usage_hard_limit_percent"], 78)

    async def test_buyer_cannot_dismiss_another_users_stopped_adset(self):
        async with self.test_session_maker() as session:
            admin = (await session.execute(select(User).where(User.telegram_id == "8634201356"))).scalar_one()
            session.add(
                Account(
                    account_id="act_admin_account",
                    name="Admin account",
                    access_token="admin_mock_token",
                    owner_user_id=admin.id,
                    workspace_id=admin.active_workspace_id,
                    timezone_name="UTC",
                )
            )
            session.add(
                StoppedAdSet(
                    account_id="act_admin_account",
                    adset_id="admin_adset_1",
                    adset_name="Admin ad set",
                    stop_spend=10.0,
                )
            )
            await session.commit()

        buyer_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            {"id": 8948797431, "first_name": "Nick", "username": "buyer_nick"},
        )
        admin_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            {"id": 8634201356, "first_name": "Admin", "username": "admin_user"},
        )
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            forbidden = await client.post(
                "/api/adsets/admin_adset_1/dismiss",
                headers={"Authorization": f"tma {buyer_data}"},
            )
            allowed = await client.post(
                "/api/adsets/admin_adset_1/dismiss",
                headers={"Authorization": f"tma {admin_data}"},
            )

        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(allowed.status_code, 200)

        async with self.test_session_maker() as session:
            audit_event = (
                await session.execute(
                    select(AuditEvent).where(AuditEvent.event_type == "HIDE_STOPPED_NOTIFICATION")
                )
            ).scalar_one()
            self.assertEqual(audit_event.actor_type, "user")
            self.assertEqual(audit_event.actor_id, "8634201356")
            self.assertEqual(audit_event.adset_id, "admin_adset_1")
            self.assertEqual(audit_event.action, "HIDE_NOTIFICATION")

    async def test_audit_history_is_owner_isolated_filterable_and_paginated(self):
        async with self.test_session_maker() as session:
            buyer = (await session.execute(select(User).where(User.telegram_id == "8948797431"))).scalar_one()
            admin = (await session.execute(select(User).where(User.telegram_id == "8634201356"))).scalar_one()
            session.add_all(
                [
                    AuditEvent(
                        owner_user_id=buyer.id,
                        workspace_id=buyer.active_workspace_id,
                        category="RULE_ACTION",
                        event_type="STOP",
                        status="SUCCESS",
                        account_id="act_1018756607700064",
                        account_name="Buyer account",
                        adset_id="buyer_adset",
                        adset_name="Buyer ad set",
                        rule_name="Buyer stop rule",
                        action="STOP",
                        message="Buyer event",
                        correlation_id="buyer-cycle",
                    ),
                    AuditEvent(
                        owner_user_id=admin.id,
                        workspace_id=admin.active_workspace_id,
                        category="ACCOUNT_HEALTH",
                        event_type="TOKEN_EXPIRED",
                        status="ERROR",
                        account_id="act_admin_account",
                        account_name="Admin account",
                        message="Admin event",
                        correlation_id="admin-cycle",
                    ),
                ]
            )
            await session.commit()

        buyer_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            {"id": 8948797431, "first_name": "Nick", "username": "buyer_nick"},
        )
        admin_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            {"id": 8634201356, "first_name": "Admin", "username": "admin_user"},
        )
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            buyer_response = await client.get(
                "/api/audit-events?category=rule_action&search=Buyer",
                headers={"Authorization": f"tma {buyer_data}"},
            )
            buyer_error_filter = await client.get(
                "/api/audit-events?status=ERROR",
                headers={"Authorization": f"tma {buyer_data}"},
            )
            admin_response = await client.get(
                "/api/audit-events?page_size=1",
                headers={"Authorization": f"tma {admin_data}"},
            )

        self.assertEqual(buyer_response.status_code, 200)
        self.assertEqual(buyer_response.json()["total"], 1)
        self.assertEqual(buyer_response.json()["items"][0]["adset_id"], "buyer_adset")
        self.assertIsNone(buyer_response.json()["items"][0]["owner_user_id"])
        self.assertEqual(buyer_response.json()["status_counts"]["SUCCESS"], 1)
        self.assertEqual(buyer_error_filter.json()["total"], 0)
        self.assertEqual(buyer_error_filter.json()["status_counts"]["SUCCESS"], 1)

        self.assertEqual(admin_response.status_code, 200)
        self.assertEqual(admin_response.json()["total"], 2)
        self.assertEqual(admin_response.json()["total_pages"], 2)
        self.assertEqual(len(admin_response.json()["items"]), 1)
        self.assertIsNotNone(admin_response.json()["items"][0]["owner_user_id"])

    async def test_failed_manual_reactivation_is_audited_without_exposing_secret(self):
        async with self.test_session_maker() as session:
            session.add(
                StoppedAdSet(
                    account_id="act_1018756607700064",
                    adset_id="buyer_failed_adset",
                    adset_name="Buyer failed ad set",
                    stop_spend=8.0,
                )
            )
            await session.commit()

        buyer_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            {"id": 8948797431, "first_name": "Nick", "username": "buyer_nick"},
        )
        transport = httpx.ASGITransport(app=self.app)
        with patch.object(
            api_routes_module.meta_client,
            "set_adset_status",
            new=AsyncMock(side_effect=RuntimeError("access_token=private-secret")),
        ):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/adsets/buyer_failed_adset/reactivate",
                    headers={"Authorization": f"tma {buyer_data}"},
                )

        self.assertEqual(response.status_code, 500)
        self.assertNotIn("private-secret", response.text)
        async with self.test_session_maker() as session:
            event = (
                await session.execute(
                    select(AuditEvent).where(AuditEvent.adset_id == "buyer_failed_adset")
                )
            ).scalar_one()
            stopped = (
                await session.execute(
                    select(StoppedAdSet).where(StoppedAdSet.adset_id == "buyer_failed_adset")
                )
            ).scalar_one()
            self.assertEqual(event.status, "ERROR")
            self.assertIn("access_token=[REDACTED]", event.message)
            self.assertFalse(stopped.is_resolved)

    async def test_stop_undo_is_guarded_idempotent_and_append_only(self):
        async with self.test_session_maker() as session:
            buyer = (await session.execute(select(User).where(User.telegram_id == "8948797431"))).scalar_one()
            source = AuditEvent(
                owner_user_id=buyer.id,
                workspace_id=buyer.active_workspace_id,
                actor_type="system",
                actor_id="monitoring_worker",
                category="RULE_ACTION",
                event_type="STOP",
                status="SUCCESS",
                account_id="act_1018756607700064",
                account_name="Швеция 1",
                adset_id="undo_stop_adset",
                adset_name="Undo stop",
                action="STOP",
                before_state={"status":"ACTIVE"},
                after_state={"status":"PAUSED"},
                correlation_id="source-stop",
            )
            session.add(source)
            session.add(
                StoppedAdSet(
                    account_id="act_1018756607700064",
                    adset_id="undo_stop_adset",
                    adset_name="Undo stop",
                    stop_spend=12.0,
                )
            )
            await session.commit()
            await session.refresh(source)
            source_id = source.id

        buyer_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            {"id": 8948797431, "first_name": "Nick", "username": "buyer_nick"},
        )
        headers = {"Authorization": f"tma {buyer_data}"}
        transport = httpx.ASGITransport(app=self.app)
        current_state = {
            "adset_id": "undo_stop_adset",
            "adset_name": "Undo stop",
            "status": "PAUSED",
            "effective_status": "PAUSED",
            "daily_budget": 50.0,
        }
        with (
            patch.object(
                api_routes_module.meta_client,
                "get_adset_state",
                new=AsyncMock(return_value=current_state),
            ) as get_state,
            patch.object(
                api_routes_module.meta_client,
                "set_adset_status",
                new=AsyncMock(return_value=True),
            ) as set_status,
        ):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                first = await client.post(f"/api/audit-events/{source_id}/undo", headers=headers)
                second = await client.post(f"/api/audit-events/{source_id}/undo", headers=headers)
                history = await client.get("/api/audit-events?page_size=100", headers=headers)

        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.json()["already_reverted"])
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["already_reverted"])
        get_state.assert_awaited_once_with("undo_stop_adset", "mock_token", currency="USD")
        set_status.assert_awaited_once_with("undo_stop_adset", "mock_token", "ACTIVE")
        source_item = next(item for item in history.json()["items"] if item["id"] == source_id)
        self.assertEqual(source_item["display_status"], "REVERTED")
        self.assertFalse(source_item["can_undo"])
        self.assertIsNotNone(source_item["reverted_by_event_id"])

        async with self.test_session_maker() as session:
            source_row = await session.get(AuditEvent, source_id)
            reversal = (
                await session.execute(
                    select(AuditEvent).where(AuditEvent.reverts_event_id == source_id)
                )
            ).scalar_one()
            stopped = (
                await session.execute(
                    select(StoppedAdSet).where(StoppedAdSet.adset_id == "undo_stop_adset")
                )
            ).scalar_one()
            self.assertEqual(source_row.status, "SUCCESS")
            self.assertEqual(reversal.event_type, "UNDO_ACTION")
            after_st = json.loads(reversal.after_state) if isinstance(reversal.after_state, str) else reversal.after_state
            self.assertEqual(after_st["status"], "ACTIVE")
            self.assertTrue(stopped.is_resolved)

    async def test_undo_rejects_a_stale_action_after_a_newer_mutation(self):
        async with self.test_session_maker() as session:
            buyer = (await session.execute(select(User).where(User.telegram_id == "8948797431"))).scalar_one()
            source = AuditEvent(
                owner_user_id=buyer.id,
                workspace_id=buyer.active_workspace_id,
                category="RULE_ACTION",
                event_type="STOP",
                status="SUCCESS",
                account_id="act_1018756607700064",
                adset_id="newer_action_adset",
                action="STOP",
                before_state={"status":"ACTIVE"},
                after_state={"status":"PAUSED"},
                correlation_id="old-action",
            )
            session.add(source)
            await session.flush()
            session.add(
                AuditEvent(
                    owner_user_id=buyer.id,
                    workspace_id=buyer.active_workspace_id,
                    category="MANUAL_ACTION",
                    event_type="MANUAL_REACTIVATE",
                    status="SUCCESS",
                    account_id="act_1018756607700064",
                    adset_id="newer_action_adset",
                    action="REACTIVATE_ADSET",
                    before_state={"status":"PAUSED"},
                    after_state={"status":"ACTIVE"},
                    correlation_id="new-action",
                )
            )
            await session.commit()
            source_id = source.id

        buyer_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            {"id": 8948797431, "first_name": "Nick", "username": "buyer_nick"},
        )
        with patch.object(
            api_routes_module.meta_client,
            "get_adset_state",
            new=AsyncMock(),
        ) as get_state:
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/api/audit-events/{source_id}/undo",
                    headers={"Authorization": f"tma {buyer_data}"},
                )

        self.assertEqual(response.status_code, 409)
        self.assertIn("уже изменялся", response.json()["detail"])
        get_state.assert_not_awaited()

    async def test_budget_undo_restores_the_exact_previous_value(self):
        async with self.test_session_maker() as session:
            buyer = (await session.execute(select(User).where(User.telegram_id == "8948797431"))).scalar_one()
            source = AuditEvent(
                owner_user_id=buyer.id,
                workspace_id=buyer.active_workspace_id,
                category="RULE_ACTION",
                event_type="INCREASE_BUDGET",
                status="SUCCESS",
                account_id="act_1018756607700064",
                adset_id="undo_budget_adset",
                action="INCREASE_BUDGET",
                before_state={"daily_budget":50.0},
                after_state={"daily_budget":60.0},
                correlation_id="budget-action",
            )
            session.add(source)
            await session.commit()
            await session.refresh(source)
            source_id = source.id

        buyer_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            {"id": 8948797431, "first_name": "Nick", "username": "buyer_nick"},
        )
        with (
            patch.object(
                api_routes_module.meta_client,
                "get_adset_state",
                new=AsyncMock(return_value={"status": "ACTIVE", "daily_budget": 60.0}),
            ),
            patch.object(
                api_routes_module.meta_client,
                "update_adset_budget",
                new=AsyncMock(return_value=True),
            ) as update_budget,
        ):
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/api/audit-events/{source_id}/undo",
                    headers={"Authorization": f"tma {buyer_data}"},
                )

        self.assertEqual(response.status_code, 200)
        update_budget.assert_awaited_once_with(
            "undo_budget_adset", "mock_token", 50.0, currency="USD"
        )

    async def test_account_cannot_attach_another_owners_preset(self):
        async with self.test_session_maker() as session:
            admin = (await session.execute(select(User).where(User.telegram_id == "8634201356"))).scalar_one()
            foreign_preset = RulePreset(
                owner_user_id=admin.id,
                workspace_id=admin.active_workspace_id,
                name="Admin-only preset",
                action="turn_off",
                conditions=[],
            )
            session.add(foreign_preset)
            await session.commit()
            await session.refresh(foreign_preset)
            preset_id = foreign_preset.id

        buyer_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            {"id": 8948797431, "first_name": "Nick", "username": "buyer_nick"},
        )
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/accounts/act_1018756607700064/assign-rule",
                headers={"Authorization": f"tma {buyer_data}"},
                json={"preset_id": preset_id},
            )

        self.assertEqual(response.status_code, 404)

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

    async def test_unauthorized_direct_access_blocked(self):
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Request without Telegram initData header
            resp = await client.get("/api/me")
            self.assertEqual(resp.status_code, 401)

    async def test_security_headers_present(self):
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/live")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.headers.get("x-content-type-options"), "nosniff")
            self.assertEqual(resp.headers.get("referrer-policy"), "strict-origin-when-cross-origin")
            self.assertEqual(resp.headers.get("x-xss-protection"), "1; mode=block")

    async def test_otp_request_rate_limiting(self):
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("api.routers.auth.send_otp_verification_email", new=AsyncMock()):
                first = await client.post(
                    "/api/auth/request-temporary-password",
                    json={"email": "ratelimit@example.com"},
                )
                self.assertEqual(first.status_code, 200)

                second = await client.post(
                    "/api/auth/request-temporary-password",
                    json={"email": "ratelimit@example.com"},
                )
                self.assertEqual(second.status_code, 429)

    async def test_otp_request_delivery_failure_raises_502(self):
        transport = httpx.ASGITransport(app=self.app)
        orig_key = settings.RESEND_API_KEY
        settings.RESEND_API_KEY = "re_test_dummy_key"
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                with patch("api.routers.auth.send_otp_verification_email", new=AsyncMock(return_value=False)):
                    resp = await client.post(
                        "/api/auth/request-temporary-password",
                        json={"email": "deliveryfail@example.com"},
                    )
                    self.assertEqual(resp.status_code, 502)
                    self.assertIn("Не удалось доставить письмо", resp.json()["detail"])
        finally:
            settings.RESEND_API_KEY = orig_key

    async def test_otp_brute_force_lockout(self):
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("api.routers.auth.send_otp_verification_email", new=AsyncMock()):
                req = await client.post(
                    "/api/auth/request-temporary-password",
                    json={"email": "bruteforce@example.com"},
                )
                self.assertEqual(req.status_code, 200)

            # 4 failed attempts
            for _ in range(4):
                failed = await client.post(
                    "/api/auth/login",
                    json={"username": "bruteforce@example.com", "password": "000000"},
                )
                self.assertEqual(failed.status_code, 401)

            # 5th failed attempt locks out the OTP
            fifth = await client.post(
                "/api/auth/login",
                json={"username": "bruteforce@example.com", "password": "000000"},
            )
            self.assertEqual(fifth.status_code, 401)
            self.assertIn("Превышено максимальное количество попыток", fifth.json()["detail"])

            # Verify the code is now marked is_used in the database
            async with self.test_session_maker() as session:
                code_record = (
                    await session.execute(
                        select(EmailVerificationCode).where(
                            EmailVerificationCode.email == "bruteforce@example.com"
                        )
                    )
                ).scalar_one()
                self.assertTrue(code_record.is_used)

    async def test_avatar_and_logo_disallow_svg(self):
        buyer_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            {"id": 8948797431, "first_name": "Nick", "username": "buyer_nick"},
        )
        headers = {"Authorization": f"tma {buyer_data}"}
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            avatar_svg = await client.post(
                "/api/onboarding/avatar",
                headers=headers,
                files={"file": ("avatar.svg", b"<svg onload=alert(1)>", "image/svg+xml")},
            )
            self.assertEqual(avatar_svg.status_code, 400)

            logo_svg = await client.post(
                "/api/onboarding/workspace/logo",
                headers=headers,
                files={"file": ("logo.svg", b"<svg onload=alert(1)>", "image/svg+xml")},
            )
            self.assertEqual(logo_svg.status_code, 400)

    async def test_update_profile_avatar_url_validation(self):
        buyer_data = generate_valid_telegram_init_data(
            settings.BOT_TOKEN,
            {"id": 8948797431, "first_name": "Nick", "username": "buyer_nick"},
        )
        headers = {"Authorization": f"tma {buyer_data}"}
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Malicious schemes and XSS payloads must be rejected with 422
            for bad_avatar in (
                "javascript:alert(1)",
                "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=",
                'x" onerror="alert(1)',
                "<script>alert(1)</script>",
                "//evil.com/avatar.png",
                "ftp://example.com/avatar.png",
            ):
                res = await client.post(
                    "/api/auth/update-profile",
                    headers=headers,
                    json={"avatar_url": bad_avatar},
                )
                self.assertEqual(res.status_code, 422, f"Failed to reject bad avatar: {bad_avatar}")

            # 2. Valid HTTPS, HTTP, local /uploads/avatars/ and empty string must succeed
            for good_avatar in (
                "https://cdn.example.com/avatar.png",
                "http://cdn.example.com/avatar.jpg",
                "/uploads/avatars/avatar_123_abc.webp",
                "",
            ):
                res = await client.post(
                    "/api/auth/update-profile",
                    headers=headers,
                    json={"avatar_url": good_avatar},
                )
                self.assertEqual(res.status_code, 200, f"Failed to accept good avatar: {good_avatar}")
                self.assertEqual(res.json()["avatar_url"], good_avatar)
