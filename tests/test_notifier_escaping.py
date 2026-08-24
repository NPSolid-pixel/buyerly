import unittest
from unittest.mock import AsyncMock, patch

from bot.notifier import TelegramNotifier, format_account_day_started_message
from rules.engine import RuleAction, RuleEvaluationResult


class TestTelegramNotifierEscaping(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_bot = AsyncMock()
        self.notifier = TelegramNotifier(bot=self.mock_bot, target_chat_id="123456789")

    @patch("bot.notifier.async_session_maker")
    async def test_stop_alert_escapes_comparison_operators_in_reason(self, mock_session_maker):
        eval_result = RuleEvaluationResult(
            action=RuleAction.STOP,
            adset_id="1234567890",
            adset_name="<Test AdSet & Campaign>",
            spend=45.5,
            leads=0,
            registrations=0,
            purchases=0,
            cpl=None,
            cpreg=None,
            cpp=None,
            reason="[CTR Low Rule] CTR (< 1.5%) < 1.0% & Spend ($45.50) >= $30.00",
            currency="USD",
        )

        await self.notifier.send_alert(
            event_type="STOP",
            account_name="<MediaBuyer Account #1 & Co>",
            account_id="act_999888",
            eval_result=eval_result,
        )

        self.mock_bot.send_message.assert_called_once()
        sent_text = self.mock_bot.send_message.call_args[1]["text"]

        # Ensure no unescaped tags exist that would break Telegram HTML parser
        self.assertIn("&lt;Test AdSet &amp; Campaign&gt;", sent_text)
        self.assertIn("&lt;MediaBuyer Account #1 &amp; Co&gt;", sent_text)
        self.assertIn("CTR (&lt; 1.5%) &lt; 1.0% &amp; Spend ($45.50) &gt;= $30.00", sent_text)
        self.assertNotIn("<Test AdSet", sent_text)
        self.assertNotIn("CTR (< 1.5%)", sent_text)

    @patch("bot.notifier.async_session_maker")
    async def test_token_expired_alert_truncates_before_escaping(self, mock_session_maker):
        # Long error message with unescaped XML/HTML entities
        long_msg = "Error validating access token: <session_id>&" * 20

        await self.notifier.send_alert(
            event_type="TOKEN_EXPIRED",
            account_name="Account <Special & Name>",
            account_id="act_111222",
            subcode=459,
            subcode_title="Checkpoint <Required>",
            subcode_description="Profile sent to checkpoint <security check>",
            action_hint="Login via browser & pass selfie",
            user_msg=long_msg,
        )

        self.mock_bot.send_message.assert_called_once()
        sent_text = self.mock_bot.send_message.call_args[1]["text"]

        self.assertIn("Account &lt;Special &amp; Name&gt;", sent_text)
        self.assertIn("Checkpoint &lt;Required&gt;", sent_text)
        self.assertIn("Profile sent to checkpoint &lt;security check&gt;", sent_text)
        # Verify that truncated user_msg does not have broken HTML entities like '&l' or '&a'
        self.assertNotIn("<session_id>", sent_text)
        self.assertIn("&lt;session_id&gt;", sent_text)

    @patch("bot.notifier.async_session_maker")
    async def test_notify_only_and_budget_alerts_escape_reason(self, mock_session_maker):
        eval_result = RuleEvaluationResult(
            action=RuleAction.NOTIFY_ONLY,
            adset_id="555",
            adset_name="AdSet <A & B>",
            spend=10.0,
            leads=1,
            registrations=0,
            purchases=0,
            cpl=10.0,
            cpreg=None,
            cpp=None,
            reason="CPL ($10.00) > $5.00 & Leads (< 2) < 2",
            currency="EUR",
        )

        await self.notifier.send_alert(
            event_type="NOTIFY_ONLY",
            account_name="Account <EU>",
            account_id="act_555",
            eval_result=eval_result,
        )

        sent_text = self.mock_bot.send_message.call_args[1]["text"]
        self.assertIn("CPL ($10.00) &gt; $5.00 &amp; Leads (&lt; 2) &lt; 2", sent_text)
        self.assertIn("AdSet &lt;A &amp; B&gt;", sent_text)

    def test_format_account_day_started_message_escaping(self):
        msg = format_account_day_started_message(
            account_name="<Test & Agency>",
            account_id="act_007",
            local_date="24.08.2026",
            local_time="00:00",
            timezone_name="Europe/Kyiv <EET>",
            utc_offset="UTC+03:00 <DST>",
        )
        self.assertIn("&lt;Test &amp; Agency&gt;", msg)
        self.assertIn("Europe/Kyiv &lt;EET&gt;", msg)
        self.assertIn("UTC+03:00 &lt;DST&gt;", msg)
        self.assertNotIn("<Test & Agency>", msg)


if __name__ == "__main__":
    unittest.main()
