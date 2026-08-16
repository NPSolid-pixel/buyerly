from pathlib import Path
import unittest


class TestFrontendRuleContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        webapp = Path(__file__).parents[1] / "webapp"
        cls.script = (webapp / "js" / "app.js").read_text()
        cls.index = (webapp / "index.html").read_text()
        cls.styles = (webapp / "css" / "styles.css").read_text()

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

    def test_account_import_does_not_offer_or_send_automatic_rules(self):
        for removed_contract in (
            "addEnableRulesSwitch",
            "selectedPreset",
            "max_spend_0_leads",
            "max_spend_1_lead",
            "max_cpa_multiple_leads",
            "Начальные лимиты автоправил",
        ):
            self.assertNotIn(removed_contract, self.script + self.index)

        self.assertIn("btnBatchOpenRules", self.index)
        self.assertIn("Перейти к правилам", self.index)

    def test_telegram_mini_app_sends_signed_init_data(self):
        sdk_position = self.index.index("telegram-web-app.js")
        app_position = self.index.index("/static/js/app.js")

        self.assertLess(sdk_position, app_position)
        self.assertIn("window.Telegram?.WebApp?.initData", self.script)
        self.assertIn("`tma ${telegramInitData}`", self.script)
        self.assertIn("!authToken && !telegramInitData", self.script)

    def test_desktop_shell_uses_quiet_palette_and_sidebar_grid(self):
        self.assertIn("QUIET GRAPHITE PALETTE", self.styles)
        self.assertIn("grid-template-columns: 232px minmax(0, 1fr)", self.styles)
        self.assertIn("--tg-bg: #0d0e11", self.styles)
        self.assertNotIn("TOKYO NIGHT COLOR PALETTE", self.styles)
        self.assertNotIn("appEl.style.display = 'block'", self.script)

    def test_logs_are_a_first_class_section_not_a_summary_table(self):
        self.assertIn('data-tab="logs"', self.index)
        self.assertIn('id="tab-logs"', self.index)
        self.assertIn('id="logsAttentionBanner"', self.index)
        self.assertNotIn('id="stoppedAdsetsSection"', self.index)
        self.assertIn('/api/audit-events?', self.script)
        self.assertIn('window.openLogDetails', self.script)

    def test_mobile_settings_remain_reachable_from_profile_badge(self):
        self.assertIn('id="userBadge"', self.index)
        self.assertIn('role="button" tabindex="0"', self.index)
        self.assertIn("window.switchTab('settings')", self.script)
        self.assertNotIn('class="mobile-nav-item" data-tab="settings"', self.index)
