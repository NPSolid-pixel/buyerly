from pathlib import Path
import unittest


class TestFrontendRuleContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = (Path(__file__).parents[1] / "webapp" / "js" / "app.js").read_text()

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
