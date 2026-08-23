import json
import unittest
from core.metrics import validate_rule_semantics
from database.models import Account
from rules.engine import RuleEngine, RuleAction

class TestRuleEngine(unittest.TestCase):

    def setUp(self):
        self.account = Account(
            account_id="act_test_123",
            name="Тестовый кабинет",
            access_token="mock_token",
            timezone_name="UTC",
            currency="USD",
            rules_enabled=True,
            active_rules="[]",
        )

    def set_rule(
        self,
        *,
        action="turn_off",
        conditions=None,
        logic="and",
        budget_change_percent=0.0,
        budget_max_daily=0.0,
    ):
        """Configure one rule using the current multi-rule account schema."""
        self.account.active_rules = json.dumps(
            [
                {
                    "preset_id": 1,
                    "name": "Test rule",
                    "action": action,
                    "conditions": conditions or [],
                    "logic": logic,
                    "cooldown_minutes": 0,
                    "notify_tg": True,
                    "budget_change_percent": budget_change_percent,
                    "budget_max_daily": budget_max_daily,
                }
            ]
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

    def test_unknown_action_fails_closed_instead_of_stopping(self):
        self.set_rule(
            action="delete_account",
            conditions=[{"metric": "spend", "operator": "gte", "value": 1.0}],
        )
        adset = {
            "adset_id": "1",
            "adset_name": "Test",
            "status": "ACTIVE",
            "effective_status": "ACTIVE",
            "spend": 100.0,
            "leads": 0,
            "registrations": 0,
        }
        result = RuleEngine.evaluate(adset, self.account)
        self.assertEqual(result.action, RuleAction.NOOP)
        self.assertIn("некорректны", result.reason)

    def test_invalid_logic_window_and_non_finite_value_fail_closed(self):
        adset = {
            "adset_id": "1",
            "adset_name": "Test",
            "status": "ACTIVE",
            "effective_status": "ACTIVE",
            "spend": 100.0,
            "leads": 0,
            "registrations": 0,
        }
        invalid_rules = [
            {
                "action": "turn_off",
                "logic": "xor",
                "conditions": [{"metric": "spend", "operator": "gte", "value": 1.0}],
            },
            {
                "action": "turn_off",
                "logic": "and",
                "conditions": [
                    {"metric": "spend", "operator": "gte", "value": 1.0, "time_window": "lifetime"}
                ],
            },
            {
                "action": "turn_off",
                "logic": "and",
                "conditions": [{"metric": "spend", "operator": "gte", "value": float("nan")}],
            },
        ]
        for invalid_rule in invalid_rules:
            self.account.active_rules = json.dumps([{**invalid_rule, "preset_id": 99, "name": "Invalid"}])
            self.assertEqual(RuleEngine.evaluate(adset, self.account).action, RuleAction.NOOP)

    def test_inactive_adset_is_ignored_by_turn_off(self):
        """A paused ad set must not receive actions intended for active delivery."""
        self.set_rule(conditions=[{"metric": "spend", "operator": "gte", "value": 1.0}])
        adset = {"adset_id": "1", "adset_name": "Test", "status": "PAUSED", "spend": 100.0, "leads": 0, "registrations": 0}
        res = RuleEngine.evaluate(adset, self.account)
        self.assertEqual(res.action, RuleAction.NOOP)

    # --------------------------------------------------------
    # Метрика: spend
    # --------------------------------------------------------

    def test_spend_gte_turn_off(self):
        """Спенд >= $15 → STOP."""
        self.set_rule(
            action="turn_off",
            conditions=[{"metric": "spend", "operator": "gte", "value": 15.0}],
        )
        
        adset_under = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 10.0, "leads": 2, "registrations": 0}
        self.assertEqual(RuleEngine.evaluate(adset_under, self.account).action, RuleAction.NOOP)

        adset_over = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 15.5, "leads": 2, "registrations": 0}
        res = RuleEngine.evaluate(adset_over, self.account)
        self.assertEqual(res.action, RuleAction.STOP)
        self.assertIn("Спенд (15.50 USD) ≥ 15.00 USD", res.reason)

    # --------------------------------------------------------
    # Метрика: cpl (цена за лид)
    # --------------------------------------------------------

    def test_cpl_notify_only(self):
        """CPL >= $7 → NOTIFY_ONLY."""
        self.set_rule(
            action="notify_only",
            conditions=[{"metric": "cpl", "operator": "gte", "value": 7.0}],
        )
        
        adset = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 20.0, "leads": 2, "registrations": 0}
        res = RuleEngine.evaluate(adset, self.account)
        self.assertEqual(res.action, RuleAction.NOTIFY_ONLY)
        self.assertIn("Цена за лид (CPL) (10.00 USD) ≥ 7.00 USD", res.reason)

    # --------------------------------------------------------
    # Метрика: leads (количество лидов)
    # --------------------------------------------------------

    def test_leads_count_metric(self):
        """Лиды > 3 И CPL < $5 → STOP."""
        self.set_rule(
            conditions=[
                {"metric": "leads", "operator": "gte", "value": 3.0},
                {"metric": "cpl", "operator": "lt", "value": 5.0},
            ]
        )
        
        # 4 leads, CPL = $3 → match
        adset_match = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 12.0, "leads": 4, "registrations": 0}
        self.assertEqual(RuleEngine.evaluate(adset_match, self.account).action, RuleAction.STOP)

        # 2 leads — not >= 3 → NOOP
        adset_no_match = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 6.0, "leads": 2, "registrations": 0}
        self.assertEqual(RuleEngine.evaluate(adset_no_match, self.account).action, RuleAction.NOOP)

    # --------------------------------------------------------
    # Удалённая метрика: общий CPA
    # --------------------------------------------------------

    def test_legacy_cpa_never_triggers(self):
        """Старый CPA не должен менять Meta без ручного выбора новой метрики."""
        self.set_rule(conditions=[{"metric": "cpa", "operator": "gte", "value": 10.0}])

        adset = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 22.0, "leads": 1, "registrations": 1}
        res = RuleEngine.evaluate(adset, self.account)
        self.assertEqual(res.action, RuleAction.NOOP)

    def test_cpp_metric(self):
        """CPP remains available for non-destructive actions."""
        self.set_rule(
            action="notify_only",
            conditions=[{"metric": "cpp", "operator": "gt", "value": 10.0}],
        )
        adset = {
            "adset_id": "1",
            "adset_name": "Test",
            "status": "ACTIVE",
            "spend": 22.0,
            "leads": 1,
            "registrations": 1,
            "purchases": 2,
        }
        res = RuleEngine.evaluate(adset, self.account)
        self.assertEqual(res.action, RuleAction.NOTIFY_ONLY)
        self.assertIn("Цена покупки (CPP) (11.00 USD) > 10.00 USD", res.reason)

    def test_lead_stop_executes_even_when_registration_exists(self):
        """Explicit STOP conditions are honored even when registrations or purchases exist."""
        self.set_rule(
            conditions=[
                {"metric": "spend", "operator": "gte", "value": 2.0},
                {"metric": "leads", "operator": "lte", "value": 1.0},
            ]
        )
        adset = {
            "adset_id": "1",
            "adset_name": "Test",
            "status": "ACTIVE",
            "effective_status": "ACTIVE",
            "spend": 6.0,
            "leads": 1,
            "registrations": 1,
            "purchases": 0,
        }

        result = RuleEngine.evaluate(adset, self.account)

        self.assertEqual(result.action, RuleAction.STOP)

    def test_lead_stop_executes_even_when_purchase_exists(self):
        self.set_rule(
            conditions=[
                {"metric": "spend", "operator": "gte", "value": 2.0},
                {"metric": "leads", "operator": "eq", "value": 0.0},
            ]
        )
        adset = {
            "adset_id": "1",
            "adset_name": "Test",
            "status": "ACTIVE",
            "effective_status": "ACTIVE",
            "spend": 6.0,
            "leads": 0,
            "registrations": 0,
            "purchases": 1,
        }

        result = RuleEngine.evaluate(adset, self.account)

        self.assertEqual(result.action, RuleAction.STOP)

    def test_explicit_deep_funnel_stop_executes(self):
        """Explicit deep-funnel STOP conditions (e.g. CPReg or Registrations limit) are evaluated."""
        self.set_rule(
            conditions=[
                {"metric": "spend", "operator": "gte", "value": 20.0},
                {"metric": "registrations", "operator": "lte", "value": 1.0},
            ]
        )
        adset = {
            "adset_id": "1",
            "adset_name": "Test",
            "status": "ACTIVE",
            "effective_status": "ACTIVE",
            "spend": 25.0,
            "leads": 2,
            "registrations": 1,
            "purchases": 0,
        }

        result = RuleEngine.evaluate(adset, self.account)
        self.assertEqual(result.action, RuleAction.STOP)

    def test_zero_event_cost_is_unavailable(self):
        """Нулевые лиды не превращают Spend в CPL и не запускают масштабирование."""
        self.set_rule(
            action="increase_budget",
            conditions=[{"metric": "cpl", "operator": "lt", "value": 10.0}],
            budget_change_percent=20.0,
        )
        adset = {
            "adset_id": "1",
            "adset_name": "Test",
            "status": "ACTIVE",
            "spend": 5.0,
            "leads": 0,
            "registrations": 0,
            "purchases": 0,
        }
        result = RuleEngine.evaluate(adset, self.account)
        self.assertEqual(result.action, RuleAction.NOOP)
        self.assertIsNone(result.cpl)

    def test_zero_spend_with_event_has_zero_cost(self):
        """Реальный нулевой CPL не смешивается с отсутствующим значением."""
        self.set_rule(
            action="notify_only",
            conditions=[{"metric": "cpl", "operator": "eq", "value": 0.0}],
        )
        adset = {
            "adset_id": "1",
            "adset_name": "Test",
            "status": "ACTIVE",
            "spend": 0.0,
            "leads": 1,
            "registrations": 0,
            "purchases": 0,
        }
        result = RuleEngine.evaluate(adset, self.account)
        self.assertEqual(result.action, RuleAction.NOTIFY_ONLY)
        self.assertEqual(result.cpl, 0.0)

    # --------------------------------------------------------
    # Метрика: ctr
    # --------------------------------------------------------

    def test_ctr_metric(self):
        """CTR < 1.0% → NOTIFY_ONLY."""
        self.set_rule(
            action="notify_only",
            conditions=[{"metric": "ctr", "operator": "lt", "value": 1.0}],
        )
        
        adset = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 10.0, "leads": 0, "registrations": 0, "impressions": 200, "clicks": 1}
        res = RuleEngine.evaluate(adset, self.account)
        self.assertEqual(res.action, RuleAction.NOTIFY_ONLY)
        self.assertIn("CTR", res.reason)

    # --------------------------------------------------------
    # AND-логика (все условия должны совпасть)
    # --------------------------------------------------------

    def test_and_logic_all_match(self):
        """AND: Спенд >= $10 И CPReg >= $5 → NOTIFY_ONLY."""
        self.set_rule(
            action="notify_only",
            logic="and",
            conditions=[
                {"metric": "spend", "operator": "gte", "value": 10.0},
                {"metric": "cpreg", "operator": "gte", "value": 5.0},
            ],
        )
        
        adset_match = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 12.0, "leads": 0, "registrations": 1}
        self.assertEqual(
            RuleEngine.evaluate(adset_match, self.account).action,
            RuleAction.NOTIFY_ONLY,
        )

        adset_no_match = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 8.0, "leads": 0, "registrations": 1}
        self.assertEqual(RuleEngine.evaluate(adset_no_match, self.account).action, RuleAction.NOOP)

    def test_strict_and_inclusive_operators_differ_at_boundary(self):
        adset = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 10.0, "leads": 1, "registrations": 0}

        self.set_rule(conditions=[{"metric": "spend", "operator": "gt", "value": 10.0}])
        self.assertEqual(RuleEngine.evaluate(adset, self.account).action, RuleAction.NOOP)

        self.set_rule(conditions=[{"metric": "spend", "operator": "gte", "value": 10.0}])
        self.assertEqual(RuleEngine.evaluate(adset, self.account).action, RuleAction.STOP)

        self.set_rule(conditions=[{"metric": "spend", "operator": "lt", "value": 10.0}])
        self.assertEqual(RuleEngine.evaluate(adset, self.account).action, RuleAction.NOOP)

        self.set_rule(conditions=[{"metric": "spend", "operator": "lte", "value": 10.0}])
        self.assertEqual(RuleEngine.evaluate(adset, self.account).action, RuleAction.STOP)

    # --------------------------------------------------------
    # OR-логика (достаточно одного условия)
    # --------------------------------------------------------

    def test_or_logic_one_match(self):
        """OR: Спенд >= $20 ИЛИ Лиды >= 5 → STOP. Только спенд совпал."""
        self.set_rule(
            logic="or",
            conditions=[
                {"metric": "spend", "operator": "gte", "value": 20.0},
                {"metric": "leads", "operator": "gte", "value": 5.0},
            ],
        )
        
        # Spend $25 (>= 20), leads = 1 (not >= 5) → OR → STOP
        adset = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 25.0, "leads": 1, "registrations": 0}
        self.assertEqual(RuleEngine.evaluate(adset, self.account).action, RuleAction.STOP)

    def test_or_logic_no_match(self):
        """OR: ни одно не совпало → NOOP."""
        self.set_rule(
            logic="or",
            conditions=[
                {"metric": "spend", "operator": "gte", "value": 20.0},
                {"metric": "leads", "operator": "gte", "value": 5.0},
            ],
        )
        
        adset = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 10.0, "leads": 2, "registrations": 0}
        self.assertEqual(RuleEngine.evaluate(adset, self.account).action, RuleAction.NOOP)

    # --------------------------------------------------------
    # Действие: increase_budget
    # --------------------------------------------------------

    def test_increase_budget_action(self):
        """CPL < $5 → INCREASE_BUDGET с процентом и потолком."""
        self.set_rule(
            action="increase_budget",
            conditions=[{"metric": "cpl", "operator": "lt", "value": 5.0}],
            budget_change_percent=20.0,
            budget_max_daily=500.0,
        )
        
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
        self.set_rule(
            action="decrease_budget",
            conditions=[{"metric": "cpl", "operator": "gte", "value": 15.0}],
            budget_change_percent=30.0,
        )
        
        adset = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 45.0, "leads": 2, "registrations": 0}
        res = RuleEngine.evaluate(adset, self.account)
        self.assertEqual(res.action, RuleAction.DECREASE_BUDGET)
        self.assertEqual(res.budget_change_percent, 30.0)

    # --------------------------------------------------------
    # Действие: turn_on (реактивация)
    # --------------------------------------------------------

    def test_turn_on_action(self):
        """turn_on → AUTO_REACTIVATE."""
        self.set_rule(
            action="turn_on",
            conditions=[{"metric": "leads", "operator": "gte", "value": 1.0}],
        )
        
        adset = {"adset_id": "1", "adset_name": "Test", "status": "PAUSED", "spend": 5.0, "leads": 2, "registrations": 0}
        res = RuleEngine.evaluate(adset, self.account)
        self.assertEqual(res.action, RuleAction.AUTO_REACTIVATE)

    def test_turn_on_does_not_touch_active_adset(self):
        self.set_rule(
            action="turn_on",
            conditions=[{"metric": "leads", "operator": "gte", "value": 1.0}],
        )

        adset = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 5.0, "leads": 2, "registrations": 0}
        res = RuleEngine.evaluate(adset, self.account)
        self.assertEqual(res.action, RuleAction.NOOP)

    # --------------------------------------------------------
    # Time window: проверка передачи insights_by_window
    # --------------------------------------------------------

    def test_time_window_yesterday(self):
        """Условие с time_window='yesterday' использует данные из insights_by_window."""
        self.set_rule(
            conditions=[
                {"metric": "spend", "operator": "gte", "value": 50.0, "time_window": "yesterday"}
            ]
        )
        
        adset_today = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 5.0, "leads": 0, "registrations": 0}
        adset_yesterday = {"spend": 55.0, "leads": 3, "registrations": 1}
        
        insights = {"yesterday": adset_yesterday}
        
        res = RuleEngine.evaluate(adset_today, self.account, insights_by_window=insights)
        self.assertEqual(res.action, RuleAction.STOP)
        self.assertIn("[Вчера]", res.reason)

    def test_time_window_fallback_to_today(self):
        """Если insights_by_window не содержит нужного окна, используются данные today."""
        self.set_rule(
            conditions=[
                {"metric": "spend", "operator": "gte", "value": 10.0, "time_window": "last_3d"}
            ]
        )
        
        adset_today = {"adset_id": "1", "adset_name": "Test", "status": "ACTIVE", "spend": 15.0, "leads": 0, "registrations": 0}
        
        # No insights_by_window → fallback to today data
        res = RuleEngine.evaluate(adset_today, self.account)
        self.assertEqual(res.action, RuleAction.STOP)

    def test_cpreg_and_cpp_turn_off_validation_allowed(self):
        """Validating turn_off action with cpreg / cpp must succeed without throwing."""
        validate_rule_semantics(
            [{"metric": "cpreg", "operator": "gte", "value": 15.0}],
            logic="and",
            action="turn_off",
        )
        validate_rule_semantics(
            [{"metric": "cpp", "operator": "gt", "value": 30.0}],
            logic="and",
            action="turn_off",
        )


if __name__ == "__main__":
    unittest.main()
