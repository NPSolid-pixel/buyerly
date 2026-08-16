from pathlib import Path
import unittest


class TestFrontendRuleContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        webapp = Path(__file__).parents[1] / "webapp"
        cls.script = (webapp / "js" / "app.js").read_text()
        cls.index = (webapp / "index.html").read_text()

    def test_frontend_uses_current_rule_endpoints(self):
        self.assertNotIn("/apply-preset", self.script)
        self.assertIn("/assign-rule", self.script)
        self.assertIn("/detach-rule/${presetId}", self.script)

    def test_account_cards_do_not_use_removed_single_rule_fields(self):
        for removed_field in (
            "acc.rule_conditions",
            "acc.preset_name",
            "acc.rule_action",
            "acc.preset_id",
        ):
            self.assertNotIn(removed_field, self.script)

    def test_telegram_mini_app_sends_signed_init_data(self):
        sdk_position = self.index.index("telegram-web-app.js")
        app_position = self.index.index("/static/js/app.js")

        self.assertLess(sdk_position, app_position)
        self.assertIn("window.Telegram?.WebApp?.initData", self.script)
        self.assertIn("`tma ${telegramInitData}`", self.script)
        self.assertIn("!authToken && !telegramInitData", self.script)
