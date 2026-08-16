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
            owner_id="123",
            rules_enabled=True,
            rule_action="turn_off",
            rule_conditions="[]",
            rule_condition_logic="and"
        )

    # --------------------------------------------------------
    # Базовые тесты: нет условий, неактивный адсет
    # --------------------------------------------------------

    def test_no_conditions_returns_noop(self):
        """Без настроенных условий — NOOP."""
        adset = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 100.0, "leads": 0, "registrations": 0}
        res = RuleEngine.evaluate(adset, self.account)
        self.assertEqual(res.action, RuleAction.NOOP)
        self.assertIn("Правила не настроены", res.reason)

    def test_inactive_adset_returns_noop(self):
        """Неактивный адсет — всегда NOOP."""
        self.account.rule_conditions = '[{"metric": "spend", "operator": "gte", "value": 1.0}]'
        adset = {"adset_id": "1", "adset_name": "Test", "status": "PAUSED", "spend": 100.0, "leads": 0, "registrations": 0}
        res = RuleEngine.evaluate(adset, self.account)
        self.assertEqual(res.action, RuleAction.NOOP)

    # --------------------------------------------------------
    # Метрика: spend
    # --------------------------------------------------------

    def test_spend_gte_turn_off(self):
        """Спенд >= $15 → STOP."""
        self.account.rule_action = "turn_off"
        self.account.rule_conditions = '[{"metric": "spend", "operator": "gte", "value": 15.0}]'
        
        adset_under = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 10.0, "leads": 2, "registrations": 0}
        self.assertEqual(RuleEngine.evaluate(adset_under, self.account).action, RuleAction.NOOP)

        adset_over = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 15.5, "leads": 2, "registrations": 0}
        res = RuleEngine.evaluate(adset_over, self.account)
        self.assertEqual(res.action, RuleAction.STOP)
        self.assertIn("Спенд ($15.50) ≥ $15.00", res.reason)

    # --------------------------------------------------------
    # Метрика: cpl (цена за лид)
    # --------------------------------------------------------

    def test_cpl_notify_only(self):
        """CPL >= $7 → NOTIFY_ONLY."""
        self.account.rule_action = "notify_only"
        self.account.rule_conditions = '[{"metric": "cpl", "operator": "gte", "value": 7.0}]'
        
        adset = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 20.0, "leads": 2, "registrations": 0}
        res = RuleEngine.evaluate(adset, self.account)
        self.assertEqual(res.action, RuleAction.NOTIFY_ONLY)
        self.assertIn("Цена за лид (CPL) ($10.00) ≥ $7.00", res.reason)

    # --------------------------------------------------------
    # Метрика: leads (количество лидов)
    # --------------------------------------------------------

    def test_leads_count_metric(self):
        """Лиды > 3 И CPL < $5 → STOP."""
        self.account.rule_action = "turn_off"
        self.account.rule_conditions = '[{"metric": "leads", "operator": "gte", "value": 3.0}, {"metric": "cpl", "operator": "lt", "value": 5.0}]'
        
        # 4 leads, CPL = $3 → match
        adset_match = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 12.0, "leads": 4, "registrations": 0}
        self.assertEqual(RuleEngine.evaluate(adset_match, self.account).action, RuleAction.STOP)

        # 2 leads — not >= 3 → NOOP
        adset_no_match = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 6.0, "leads": 2, "registrations": 0}
        self.assertEqual(RuleEngine.evaluate(adset_no_match, self.account).action, RuleAction.NOOP)

    # --------------------------------------------------------
    # Метрика: cpa (общая цена за конверсию)
    # --------------------------------------------------------

    def test_cpa_metric(self):
        """CPA > $10 → STOP."""
        self.account.rule_action = "turn_off"
        self.account.rule_conditions = '[{"metric": "cpa", "operator": "gte", "value": 10.0}]'
        
        # Spend $22, 1 lead + 1 reg = 2 conversions, CPA = $11
        adset = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 22.0, "leads": 1, "registrations": 1}
        res = RuleEngine.evaluate(adset, self.account)
        self.assertEqual(res.action, RuleAction.STOP)
        self.assertIn("CPA", res.reason)

    # --------------------------------------------------------
    # Метрика: ctr
    # --------------------------------------------------------

    def test_ctr_metric(self):
        """CTR < 1.0% → NOTIFY_ONLY."""
        self.account.rule_action = "notify_only"
        self.account.rule_conditions = '[{"metric": "ctr", "operator": "lt", "value": 1.0}]'
        
        adset = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 10.0, "leads": 0, "registrations": 0, "ctr": 0.5}
        res = RuleEngine.evaluate(adset, self.account)
        self.assertEqual(res.action, RuleAction.NOTIFY_ONLY)
        self.assertIn("CTR", res.reason)

    # --------------------------------------------------------
    # AND-логика (все условия должны совпасть)
    # --------------------------------------------------------

    def test_and_logic_all_match(self):
        """AND: Спенд >= $10 И CPR >= $5 → STOP."""
        self.account.rule_action = "turn_off"
        self.account.rule_condition_logic = "and"
        self.account.rule_conditions = '[{"metric": "spend", "operator": "gte", "value": 10.0}, {"metric": "cpr", "operator": "gte", "value": 5.0}]'
        
        adset_match = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 12.0, "leads": 0, "registrations": 1}
        self.assertEqual(RuleEngine.evaluate(adset_match, self.account).action, RuleAction.STOP)

        adset_no_match = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 8.0, "leads": 0, "registrations": 1}
        self.assertEqual(RuleEngine.evaluate(adset_no_match, self.account).action, RuleAction.NOOP)

    # --------------------------------------------------------
    # OR-логика (достаточно одного условия)
    # --------------------------------------------------------

    def test_or_logic_one_match(self):
        """OR: Спенд >= $20 ИЛИ Лиды >= 5 → STOP. Только спенд совпал."""
        self.account.rule_action = "turn_off"
        self.account.rule_condition_logic = "or"
        self.account.rule_conditions = '[{"metric": "spend", "operator": "gte", "value": 20.0}, {"metric": "leads", "operator": "gte", "value": 5.0}]'
        
        # Spend $25 (>= 20), leads = 1 (not >= 5) → OR → STOP
        adset = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 25.0, "leads": 1, "registrations": 0}
        self.assertEqual(RuleEngine.evaluate(adset, self.account).action, RuleAction.STOP)

    def test_or_logic_no_match(self):
        """OR: ни одно не совпало → NOOP."""
        self.account.rule_action = "turn_off"
        self.account.rule_condition_logic = "or"
        self.account.rule_conditions = '[{"metric": "spend", "operator": "gte", "value": 20.0}, {"metric": "leads", "operator": "gte", "value": 5.0}]'
        
        adset = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 10.0, "leads": 2, "registrations": 0}
        self.assertEqual(RuleEngine.evaluate(adset, self.account).action, RuleAction.NOOP)

    # --------------------------------------------------------
    # Действие: increase_budget
    # --------------------------------------------------------

    def test_increase_budget_action(self):
        """CPL < $5 → INCREASE_BUDGET с процентом и потолком."""
        self.account.rule_action = "increase_budget"
        self.account.rule_conditions = '[{"metric": "cpl", "operator": "lt", "value": 5.0}]'
        self.account.rule_budget_change_percent = 20.0
        self.account.rule_budget_max_daily = 500.0
        
        adset = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 8.0, "leads": 3, "registrations": 0}
        res = RuleEngine.evaluate(adset, self.account)
        self.assertEqual(res.action, RuleAction.INCREASE_BUDGET)
        self.assertEqual(res.budget_change_percent, 20.0)
        self.assertEqual(res.budget_max_daily, 500.0)

    # --------------------------------------------------------
    # Действие: decrease_budget
    # --------------------------------------------------------

    def test_decrease_budget_action(self):
        """CPL >= $15 → DECREASE_BUDGET."""
        self.account.rule_action = "decrease_budget"
        self.account.rule_conditions = '[{"metric": "cpl", "operator": "gte", "value": 15.0}]'
        self.account.rule_budget_change_percent = 30.0
        self.account.rule_budget_max_daily = 0.0
        
        adset = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 45.0, "leads": 2, "registrations": 0}
        res = RuleEngine.evaluate(adset, self.account)
        self.assertEqual(res.action, RuleAction.DECREASE_BUDGET)
        self.assertEqual(res.budget_change_percent, 30.0)

    # --------------------------------------------------------
    # Действие: turn_on (реактивация)
    # --------------------------------------------------------

    def test_turn_on_action(self):
        """turn_on → AUTO_REACTIVATE."""
        self.account.rule_action = "turn_on"
        self.account.rule_conditions = '[{"metric": "leads", "operator": "gte", "value": 1.0}]'
        
        adset = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 5.0, "leads": 2, "registrations": 0}
        res = RuleEngine.evaluate(adset, self.account)
        self.assertEqual(res.action, RuleAction.AUTO_REACTIVATE)

    # --------------------------------------------------------
    # Time window: проверка передачи insights_by_window
    # --------------------------------------------------------

    def test_time_window_yesterday(self):
        """Условие с time_window='yesterday' использует данные из insights_by_window."""
        self.account.rule_action = "turn_off"
        self.account.rule_conditions = '[{"metric": "spend", "operator": "gte", "value": 50.0, "time_window": "yesterday"}]'
        
        adset_today = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 5.0, "leads": 0, "registrations": 0}
        adset_yesterday = {"spend": 55.0, "leads": 3, "registrations": 1}
        
        insights = {"yesterday": adset_yesterday}
        
        res = RuleEngine.evaluate(adset_today, self.account, insights_by_window=insights)
        self.assertEqual(res.action, RuleAction.STOP)
        self.assertIn("[Вчера]", res.reason)

    def test_time_window_fallback_to_today(self):
        """Если insights_by_window не содержит нужного окна, используются данные today."""
        self.account.rule_action = "turn_off"
        self.account.rule_conditions = '[{"metric": "spend", "operator": "gte", "value": 10.0, "time_window": "last_3d"}]'
        
        adset_today = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 15.0, "leads": 0, "registrations": 0}
        
        # No insights_by_window → fallback to today data
        res = RuleEngine.evaluate(adset_today, self.account)
        self.assertEqual(res.action, RuleAction.STOP)


if __name__ == "__main__":
    unittest.main()
