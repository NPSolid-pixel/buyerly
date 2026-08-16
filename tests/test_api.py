import hashlib
import hmac
import json
import time
import unittest
import urllib.parse
from unittest.mock import AsyncMock, patch

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import api.auth as api_auth_module
import api.routes as api_routes_module
import api.server as api_server_module
from api.auth import validate_telegram_init_data
from api.server import create_app
from core.config import settings
from database.db import Base, verify_password
from database.models import (
    Account,
    AppSettings,
    AuditEvent,
    RuleGroup,
    RuleGroupItem,
    RulePreset,
    StoppedAdSet,
    TelegramUser,
)


def generate_valid_telegram_init_data(
    bot_token: str,
    user_dict: dict,
    *,
    auth_date: int = None,
) -> str:
    params = {
        "auth_date": str(int(time.time()) if auth_date is None else auth_date),
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
        api_server_module.async_session_maker = self.test_session_maker

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
                select(TelegramUser).where(TelegramUser.username == "buyer_nick")
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
                select(TelegramUser).where(TelegramUser.username == "buyer_nick")
            )
            upgraded_buyer = result.scalar_one()
            self.assertTrue(upgraded_buyer.password_hash.startswith("pbkdf2_sha256$"))
            self.assertTrue(verify_password(legacy_password, upgraded_buyer.password_hash))

    async def test_change_password_requires_current_password(self):
        old_password = "old-password"
        new_password = "new-password"
        async with self.test_session_maker() as session:
            result = await session.execute(
                select(TelegramUser).where(TelegramUser.username == "buyer_nick")
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
                select(TelegramUser).where(TelegramUser.username == "buyer_nick")
            )
            changed_buyer = result.scalar_one()
            self.assertTrue(verify_password(new_password, changed_buyer.password_hash))

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
            self.assertEqual(data[0]["active_rules"], [])

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

    async def test_rule_groups_are_isolated_editable_and_assigned_atomically(self):
        async with self.test_session_maker() as session:
            buyer_presets = [
                RulePreset(
                    owner_id="8948797431",
                    name="Stop no leads",
                    action="turn_off",
                    conditions=json.dumps([{"metric": "spend", "operator": "gte", "value": 10}]),
                ),
                RulePreset(
                    owner_id="8948797431",
                    name="Notify high CPL",
                    action="notify_only",
                    conditions=json.dumps([{"metric": "cpl", "operator": "gte", "value": 7}]),
                ),
            ]
            foreign_preset = RulePreset(
                owner_id="8634201356",
                name="Admin private rule",
                action="turn_off",
                conditions="[]",
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

            group_id = group["id"]
            buyer_list = await client.get("/api/rule-groups", headers=buyer_headers)
            admin_list = await client.get("/api/rule-groups", headers=admin_headers)
            forbidden_update = await client.put(
                f"/api/rule-groups/{group_id}",
                headers=admin_headers,
                json={"name": "Hijack", "preset_ids": [foreign_id]},
            )
            self.assertEqual(len(buyer_list.json()), 1)
            self.assertEqual(admin_list.json(), [])
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

    async def test_buyer_cannot_dismiss_another_users_stopped_adset(self):
        async with self.test_session_maker() as session:
            session.add(
                Account(
                    account_id="act_admin_account",
                    name="Admin account",
                    access_token="admin_mock_token",
                    owner_id="8634201356",
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
                    select(AuditEvent).where(AuditEvent.event_type == "DISMISS_STOPPED")
                )
            ).scalar_one()
            self.assertEqual(audit_event.actor_type, "user")
            self.assertEqual(audit_event.actor_id, "8634201356")
            self.assertEqual(audit_event.adset_id, "admin_adset_1")

    async def test_audit_history_is_owner_isolated_filterable_and_paginated(self):
        async with self.test_session_maker() as session:
            session.add_all(
                [
                    AuditEvent(
                        owner_id="8948797431",
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
                        owner_id="8634201356",
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
        self.assertIsNone(buyer_response.json()["items"][0]["owner_id"])
        self.assertEqual(buyer_response.json()["status_counts"]["SUCCESS"], 1)
        self.assertEqual(buyer_error_filter.json()["total"], 0)
        self.assertEqual(buyer_error_filter.json()["status_counts"]["SUCCESS"], 1)

        self.assertEqual(admin_response.status_code, 200)
        self.assertEqual(admin_response.json()["total"], 2)
        self.assertEqual(admin_response.json()["total_pages"], 2)
        self.assertEqual(len(admin_response.json()["items"]), 1)
        self.assertIsNotNone(admin_response.json()["items"][0]["owner_id"])

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

    async def test_account_cannot_attach_another_owners_preset(self):
        async with self.test_session_maker() as session:
            foreign_preset = RulePreset(
                owner_id="8634201356",
                name="Admin-only preset",
                action="turn_off",
                conditions="[]",
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
