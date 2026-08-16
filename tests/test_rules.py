import unittest
from database.models import Account
from rules.engine import RuleEngine, RuleAction

class TestRuleEngine(unittest.TestCase):

    def setUp(self):
        self.account = Account(
            account_id="act_test_123",
            name="Тестовый кабинет",
            access_token="mock_token",
            timezone_name="UTC",
            max_spend_0_leads=2.0,
            max_spend_1_lead=6.0,
            max_cpa_multiple_leads=6.0,
            conversion_event="all",
            auto_reactivate=False
        )

    def test_active_under_threshold_no_leads(self):
        adset = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 1.5, "leads": 0, "registrations": 0, "total_conversions": 0, "cpa": 0.0}
        res = RuleEngine.evaluate(adset, self.account)
        self.assertEqual(res.action, RuleAction.NOOP)

    def test_active_over_threshold_no_leads(self):
        adset = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 2.1, "leads": 0, "registrations": 0, "total_conversions": 0, "cpa": 0.0}
        res = RuleEngine.evaluate(adset, self.account)
        self.assertEqual(res.action, RuleAction.STOP)
        self.assertIn("превысил лимит $2.00", res.reason)

    def test_active_1_lead_under_threshold(self):
        adset = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 4.5, "leads": 1, "registrations": 0, "total_conversions": 1, "cpa": 4.5}
        res = RuleEngine.evaluate(adset, self.account)
        self.assertEqual(res.action, RuleAction.NOOP)

    def test_active_1_registration_over_threshold(self):
        # Проверяем, что регистрация корректно считается как конверсия
        adset = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 6.5, "leads": 0, "registrations": 1, "total_conversions": 1, "cpa": 6.5}
        res = RuleEngine.evaluate(adset, self.account)
        self.assertEqual(res.action, RuleAction.STOP)
        self.assertIn("превысил лимит $6.00", res.reason)

    def test_active_2_conversions_good_cpa(self):
        adset = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 10.0, "leads": 1, "registrations": 1, "total_conversions": 2, "cpa": 5.0}
        res = RuleEngine.evaluate(adset, self.account)
        self.assertEqual(res.action, RuleAction.NOOP)

    def test_active_2_conversions_bad_cpa(self):
        adset = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 14.0, "leads": 1, "registrations": 1, "total_conversions": 2, "cpa": 7.0}
        res = RuleEngine.evaluate(adset, self.account)
        self.assertEqual(res.action, RuleAction.STOP)
        self.assertIn("превысил допустимый порог $6.00", res.reason)

    def test_delayed_registration_reactivation_proposal(self):
        adset = {"adset_id": "1", "adset_name": "Test", "status": "PAUSED", "spend": 2.1, "leads": 0, "registrations": 1, "total_conversions": 1, "cpa": 2.1}
        res = RuleEngine.evaluate(adset, self.account, is_stopped_today=True)
        self.assertEqual(res.action, RuleAction.PROPOSE_REACTIVATE)
        self.assertIn("Долетел(а)", res.reason)

    def test_custom_rule_spend_gte_turn_off(self):
        self.account.rule_action = "turn_off"
        self.account.rule_conditions = '[{"metric": "spend", "operator": "gte", "value": 15.0}]'
        
        adset_under = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 10.0, "leads": 2, "registrations": 0}
        self.assertEqual(RuleEngine.evaluate(adset_under, self.account).action, RuleAction.NOOP)

        adset_exact = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 15.0, "leads": 2, "registrations": 0}
        res_exact = RuleEngine.evaluate(adset_exact, self.account)
        self.assertEqual(res_exact.action, RuleAction.STOP)
        self.assertIn("Спенд ($15.00) ≥ $15.00", res_exact.reason)

        adset_over = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 15.5, "leads": 2, "registrations": 0}
        res = RuleEngine.evaluate(adset_over, self.account)
        self.assertEqual(res.action, RuleAction.STOP)
        self.assertIn("Спенд ($15.50) ≥ $15.00", res.reason)

    def test_custom_rule_cpl_notify_only(self):
        self.account.rule_action = "notify_only"
        self.account.rule_conditions = '[{"metric": "cpl", "operator": "gte", "value": 7.0}]'
        
        # Spend $20, 2 leads -> CPL = $10 >= $7 -> NOTIFY_ONLY
        adset = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 20.0, "leads": 2, "registrations": 0}
        res = RuleEngine.evaluate(adset, self.account)
        self.assertEqual(res.action, RuleAction.NOTIFY_ONLY)
        self.assertIn("Цена за лид (CPL) ($10.00) ≥ $7.00", res.reason)

    def test_custom_rule_multiple_conditions_all_match(self):
        self.account.rule_action = "turn_off"
        self.account.rule_conditions = '[{"metric": "spend", "operator": "gte", "value": 10.0}, {"metric": "cpr", "operator": "gte", "value": 5.0}]'
        
        # Spend $12 (>=10), 1 reg -> CPR $12 (>=5) -> STOP
        adset_match = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 12.0, "leads": 0, "registrations": 1}
        self.assertEqual(RuleEngine.evaluate(adset_match, self.account).action, RuleAction.STOP)

        # Spend $8 (not >=10) -> NOOP
        adset_no_match = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 8.0, "leads": 0, "registrations": 1}
        self.assertEqual(RuleEngine.evaluate(adset_no_match, self.account).action, RuleAction.NOOP)

if __name__ == "__main__":
    unittest.main()
