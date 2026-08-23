import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from database.db import Base
from database.models import Account, AppSettings, AuditEvent, MetaConnection, User, Workspace, WorkspaceMember
from meta_api.client import (
    META_TOKEN_SUBCODE_MAP,
    MetaClient,
    MetaTokenAuthError,
    classify_meta_token_error,
)
from bot.notifier import TelegramNotifier
from scheduler.worker import MonitoringWorker


class TestMetaErrorSubcodesClassification(unittest.TestCase):
    """Тесты классификации субкодов ошибок Meta API и создания MetaTokenAuthError."""

    def test_known_subcodes_mapping(self):
        """Проверка корректной классификации всех ключевых субкодов Meta."""
        expected_subcodes = {
            458: "APP_REVOKED",
            459: "CHECKPOINT",
            460: "PASSWORD_CHANGED",
            463: "SESSION_EXPIRED",
            464: "UNCONFIRMED_USER",
            467: "ACCESS_TOKEN_INVALIDATED",
            490: "LOGIN_APPROVAL_NEEDED",
            492: "DEVICE_SESSION_EXPIRED",
            1348001: "ACCOUNT_PERMISSION_DENIED",
        }
        for subcode, expected_key in expected_subcodes.items():
            error_data = {
                "code": 190,
                "error_subcode": subcode,
                "message": f"Test error for {subcode}",
                "error_user_msg": f"User message for {subcode}",
                "fbtrace_id": "test_trace_123",
            }
            err = classify_meta_token_error(error_data)
            self.assertIsInstance(err, PermissionError)
            self.assertIsInstance(err, MetaTokenAuthError)
            self.assertEqual(err.subcode, subcode)
            self.assertEqual(err.subcode_key, expected_key)
            self.assertTrue(bool(err.title))
            self.assertTrue(bool(err.description))
            self.assertTrue(bool(err.action_hint))
            self.assertEqual(err.error_user_msg, f"User message for {subcode}")
            self.assertEqual(err.fbtrace_id, "test_trace_123")

    def test_string_and_none_subcodes(self):
        """Проверка безопасного приведения типов: строковый субкод и None."""
        # Строковый субкод "459"
        err_str = classify_meta_token_error({"code": 190, "error_subcode": "459", "message": "Checkpoint"})
        self.assertEqual(err_str.subcode, 459)
        self.assertEqual(err_str.subcode_key, "CHECKPOINT")

        # Отсутствующий субкод (None)
        err_none = classify_meta_token_error({"code": 190, "message": "Generic token error"})
        self.assertIsNone(err_none.subcode)
        self.assertEqual(err_none.subcode_key, "TOKEN_INVALID")

        # Невалидная строка в субкоде ("invalid")
        err_invalid = classify_meta_token_error({"code": 190, "error_subcode": "invalid"})
        self.assertIsNone(err_invalid.subcode)
        self.assertEqual(err_invalid.subcode_key, "TOKEN_INVALID")

    def test_permission_error_codes_10_and_200(self):
        """Проверка классификации кодов 10 и 200 как ошибки прав доступа."""
        err_10 = classify_meta_token_error({"code": 10, "message": "Permission Denied"})
        self.assertEqual(err_10.code, 10)
        self.assertEqual(err_10.subcode_key, "ACCOUNT_PERMISSION_DENIED")
        self.assertIn("Нет прав", err_10.title)

        err_200 = classify_meta_token_error({"code": 200, "message": "Permission Denied"})
        self.assertEqual(err_200.code, 200)
        self.assertEqual(err_200.subcode_key, "ACCOUNT_PERMISSION_DENIED")

    def test_code_102_api_session_invalid(self):
        """Проверка классификации кода 102 как ошибки API-сессии."""
        err_102 = classify_meta_token_error({"code": 102, "message": "API Session Error"})
        self.assertEqual(err_102.code, 102)
        self.assertEqual(err_102.subcode_key, "API_SESSION_INVALID")


class TestTelegramNotifierSubcodes(unittest.IsolatedAsyncioTestCase):
    """Тесты форматирования сообщений в TelegramNotifier с субкодами и защитой от XSS/HTML Injection."""

    async def test_telegram_alert_formatting_with_checkpoint(self):
        """Проверка формирования информативного алерта о чекпоинте (subcode 459)."""
        bot_mock = MagicMock()
        bot_mock.send_message = AsyncMock()
        notifier = TelegramNotifier(bot=bot_mock, target_chat_id="123456")
        notifier._save_event_log = AsyncMock()

        await notifier.send_alert(
            event_type="TOKEN_EXPIRED",
            account_name="Profit Ads 1",
            account_id="act_111222333",
            target_chat_id="123456",
            subcode=459,
            subcode_title="🔒 Чекпоинт / Бан профиля",
            subcode_description="Профиль Facebook отправлен на проверку безопасности (селфи / документы)",
            action_hint="Зайдите в профиль через антидетект-браузер и пройдите чекпоинт",
            user_msg="Your account has been temporarily locked.",
        )

        self.assertEqual(bot_mock.send_message.call_count, 1)
        _, kwargs = bot_mock.send_message.call_args
        text = kwargs["text"]
        self.assertIn("🔒 Чекпоинт / Бан профиля", text)
        self.assertIn("(Subcode 459)", text)
        self.assertIn("антидетект-браузер", text)
        self.assertIn("temporarily locked", text)
        self.assertIn("Profit Ads 1", text)
        self.assertEqual(kwargs["parse_mode"], "HTML")

    async def test_telegram_alert_html_injection_safety(self):
        """Проверка экранирования спецсимволов HTML в account_name и user_msg."""
        bot_mock = MagicMock()
        bot_mock.send_message = AsyncMock()
        notifier = TelegramNotifier(bot=bot_mock, target_chat_id="123456")
        notifier._save_event_log = AsyncMock()

        dangerous_name = "Agency <Media> & Co <script>alert(1)</script>"
        dangerous_msg = "Error validating <Token> & Session for user <12345>"

        await notifier.send_alert(
            event_type="TOKEN_EXPIRED",
            account_name=dangerous_name,
            account_id="act_999",
            target_chat_id="123456",
            subcode=463,
            subcode_title="⏳ Срок токена истёк",
            subcode_description="Истёк 60-дневный срок",
            action_hint="Обновите токен",
            user_msg=dangerous_msg,
        )

        self.assertEqual(bot_mock.send_message.call_count, 1)
        _, kwargs = bot_mock.send_message.call_args
        text = kwargs["text"]
        self.assertNotIn("<script>", text)
        self.assertNotIn("<Token>", text)
        self.assertIn("&lt;script&gt;", text)
        self.assertIn("&lt;Media&gt;", text)
        self.assertIn("&amp; Co", text)
        self.assertIn("&lt;Token&gt; &amp; Session", text)

    async def test_telegram_alert_long_message_truncation(self):
        """Проверка обрезки слишком длинного ответа Meta (защита от лимита 4096 символов)."""
        bot_mock = MagicMock()
        bot_mock.send_message = AsyncMock()
        notifier = TelegramNotifier(bot=bot_mock, target_chat_id="123456")
        notifier._save_event_log = AsyncMock()

        huge_error_msg = "A" * 1000

        await notifier.send_alert(
            event_type="TOKEN_EXPIRED",
            account_name="Test Account",
            account_id="act_123",
            target_chat_id="123456",
            subcode=460,
            subcode_title="🔑 Пароль изменён",
            subcode_description="Пароль был изменён",
            action_hint="Авторизуйтесь заново",
            user_msg=huge_error_msg,
        )

        _, kwargs = bot_mock.send_message.call_args
        text = kwargs["text"]
        # user_msg обрезается до 350 символов
        self.assertNotIn("A" * 500, text)
        self.assertIn("A" * 350, text)


from tests.test_db_helper import create_test_engine, init_test_db
from sqlalchemy.ext.asyncio import AsyncSession


class TestMonitoringWorkerTokenErrorHandling(unittest.IsolatedAsyncioTestCase):
    """Интеграционные тесты обработки MetaTokenAuthError в MonitoringWorker."""

    async def asyncSetUp(self):
        self.engine = create_test_engine()
        self.session_maker = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)
        await init_test_db(self.engine)

        async with self.session_maker() as session:
            user = User(
                id=1,
                username="buyer1",
                telegram_id="999888",
                role="admin",
            )
            workspace = Workspace(
                id=1,
                name="Main Workspace",
                slug="main",
                owner_user_id=1,
            )
            conn = MetaConnection(
                id=10,
                owner_user_id=1,
                workspace_id=1,
                provider_user_id="fb_user_1",
                provider_user_name="Facebook User",
                access_token_encrypted="gAAAAABtest...",
                status="active",
            )
            account = Account(
                id=1,
                account_id="act_777888999",
                name="Scale Campaign 1",
                owner_user_id=1,
                workspace_id=1,
                meta_connection_id=10,
                is_active=True,
                rules_enabled=True,
                account_status=1,
                currency="USD",
                timezone_name="UTC",
            )
            session.add_all([user, workspace, conn, account])
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_worker_handles_checkpoint_subcode_in_snapshot(self):
        """Воркер при получении subcode 459 деактивирует кабинет, обновляет MetaConnection и сохраняет AuditEvent."""
        from database import db
        orig_session_maker = db.async_session_maker
        db.async_session_maker = self.session_maker

        mock_meta = MagicMock()
        mock_error = MetaTokenAuthError(
            "Token expired or invalid: Checkpoint",
            code=190,
            subcode=459,
            subcode_key="CHECKPOINT",
            title="🔒 Чекпоинт / Бан профиля",
            description="Профиль Facebook отправлен на проверку безопасности",
            action_hint="Зайдите через антидетект и пройдите чекпоинт",
            error_user_msg="Please log in to continue.",
        )
        mock_meta.get_account_info = AsyncMock(side_effect=mock_error)
        mock_meta.get_adsets_and_insights = AsyncMock(return_value=[])

        sent_alerts = []
        async def mock_notifier(**kwargs):
            sent_alerts.append(kwargs)

        try:
            worker = MonitoringWorker(meta_client=mock_meta, telegram_notifier=mock_notifier)
            stats = await worker.run_cycle()

            # Проверка доставки алерта
            self.assertEqual(len(sent_alerts), 1)
            self.assertEqual(sent_alerts[0]["event_type"], "TOKEN_EXPIRED")
            self.assertEqual(sent_alerts[0]["subcode"], 459)
            self.assertIn("Чекпоинт", sent_alerts[0]["subcode_title"])
            self.assertIn("антидетект", sent_alerts[0]["action_hint"])

            # Проверка состояния БД
            async with self.session_maker() as session:
                from sqlalchemy import select
                acc = (await session.execute(select(Account).where(Account.id == 1))).scalar_one()
                self.assertFalse(acc.is_active)

                conn = (await session.execute(select(MetaConnection).where(MetaConnection.id == 10))).scalar_one()
                self.assertEqual(conn.status, "error")
                self.assertIn("безопасности", conn.last_error)

                audit = (await session.execute(select(AuditEvent).where(AuditEvent.account_id == "act_777888999"))).scalar_one()
                self.assertEqual(audit.event_type, "TOKEN_EXPIRED")
                self.assertEqual(audit.status, "ERROR")
                details = audit.details if isinstance(audit.details, dict) else json.loads(audit.details)
                self.assertEqual(details["error_subcode"], 459)
                self.assertEqual(details["subcode_key"], "CHECKPOINT")
        finally:
            db.async_session_maker = orig_session_maker


if __name__ == "__main__":
    unittest.main()
